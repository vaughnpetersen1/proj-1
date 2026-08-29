"""Real-time WebSocket ingestion.

Runs in a background thread, owns its own reconnect policy, and writes bars into
the same tables the historical backfill writes to -- so a chart drawn at 10:31am
and a backtest run at midnight read the same schema and the same provenance
columns.

What it guarantees:

  * **Reconnect.** Exponential backoff with jitter, capped, forever. A dropped
    stream is normal; a silently dead stream is not, so the socket has a read
    timeout and a heartbeat, and three missed heartbeats force a reconnect.
  * **Duplicate detection.** A bar whose timestamp is <= the last one accepted
    for that symbol is counted and dropped. Streams replay on reconnect.
  * **Gap detection.** A jump of more than one bar interval inside the regular
    session is logged, because the aggregate that follows it is wrong and the
    operator needs to know before the scanner ranks on it.
  * **Batched writes.** Messages buffer and flush on an interval, so a busy open
    is a handful of transactions rather than thousands.
  * **Honest status.** The feed label travels with every row and into the UI.
    An IEX stream is never presented as consolidated market data.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import random
import threading
import time
from typing import Any, Callable

from ..config import SETTINGS, Settings
from ..data.providers.base import Provider, ProviderUnavailable, RealtimeMarketDataProvider
from ..data.types import Bar
from ..marketstore import calendar as cal
from ..marketstore.store import MARKET, MarketStore
from .service import BAR_SECONDS, aggregate_bars

FLUSH_SECONDS = 5.0
DERIVE_SECONDS = 60.0


@dataclasses.dataclass
class StreamStats:
    state: str = "stopped"           # stopped|connecting|live|reconnecting|error|disabled
    feed: str | None = None
    provider: str | None = None
    symbols: int = 0
    channels: tuple[str, ...] = ()
    connected_at: str | None = None
    last_message_at: str | None = None
    last_bar_at: str | None = None
    messages: int = 0
    bars_written: int = 0
    duplicates: int = 0
    gaps: int = 0
    reconnects: int = 0
    last_error: str | None = None
    next_retry_in: float | None = None
    full_market_coverage: bool | None = None
    feed_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["channels"] = list(self.channels)
        return d


class RealtimeIngestor:
    def __init__(self, store: MarketStore = MARKET, settings: Settings = SETTINGS,
                 provider_lookup: Callable[[str], Provider] | None = None) -> None:
        self.store = store
        self.settings = settings
        self._lookup = provider_lookup
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = StreamStats()
        self._buffer: dict[str, list[Bar]] = {}
        self._last_ts: dict[str, dt.datetime] = {}
        self._lock = threading.Lock()
        self._last_flush = 0.0
        self._last_derive = 0.0
        self._symbols: list[str] = []
        self._channels: tuple[str, ...] = ("bars",)

    # -- provider ----------------------------------------------------------
    def _provider(self) -> RealtimeMarketDataProvider | None:
        from ..data.providers.registry import availability, get_provider
        for name in self.settings.ingest_provider_order:
            try:
                p = (self._lookup or get_provider)(name)
            except KeyError:
                continue
            if not isinstance(p, RealtimeMarketDataProvider):
                continue
            ok, _ = availability(p)
            if ok:
                return p
        return None

    # -- lifecycle ---------------------------------------------------------
    def start(self, symbols: list[str] | None = None,
              channels: tuple[str, ...] | None = None) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return {"ok": False, "reason": "already running", "status": self.status()}
        syms = [s.upper() for s in (symbols or self.settings.realtime_symbols
                                    or self.store.symbol_list()[:30])]
        if not syms:
            return {"ok": False, "reason": "no symbols to subscribe to"}
        provider = self._provider()
        if provider is None:
            self.stats = StreamStats(state="disabled", symbols=len(syms),
                                     last_error="no realtime provider is configured or "
                                                "reachable (set ALPACA_API_KEY / "
                                                "ALPACA_API_SECRET)")
            self._publish()
            return {"ok": False, "reason": self.stats.last_error}

        self._symbols = syms
        self._channels = channels or self.settings.realtime_channels
        self._stop.clear()
        self.stats = StreamStats(
            state="connecting", provider=provider.name, feed=provider.feed_label(),
            symbols=len(syms), channels=tuple(self._channels),
            full_market_coverage=getattr(provider, "is_full_market", lambda: None)(),
            feed_note=getattr(provider, "feed_description", lambda: None)())
        self._publish()
        self._thread = threading.Thread(target=self._run, args=(provider,), daemon=True,
                                        name="realtime-ingest")
        self._thread.start()
        return {"ok": True, "status": self.status()}

    def stop(self, timeout: float = 5.0) -> dict[str, Any]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._flush(force=True)
        self.stats.state = "stopped"
        self.stats.next_retry_in = None
        self._publish()
        return {"ok": True, "status": self.status()}

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict[str, Any]:
        d = self.stats.to_dict()
        d["running"] = self.is_running()
        d["buffered_bars"] = sum(len(v) for v in self._buffer.values())
        return d

    def _publish(self) -> None:
        self.store.kv_set("realtime_status", self.status())

    # -- main loop ---------------------------------------------------------
    def _run(self, provider: RealtimeMarketDataProvider) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self.stats.state = "connecting"
                self._publish()
                provider.stream(self._symbols, self._on_message,
                                channels=tuple(self._channels), stop=self._stop,
                                on_status=self._on_status)
                # a clean return means stop() was requested
                break
            except Exception as exc:                       # noqa: BLE001
                if self._stop.is_set():
                    break
                self.stats.state = "reconnecting"
                self.stats.reconnects += 1
                self.stats.last_error = f"{type(exc).__name__}: {exc}"
                # full jitter: avoids every client reconnecting in lockstep after
                # a vendor-side blip
                wait = min(backoff, self.settings.reconnect_max_backoff)
                wait = random.uniform(0.5 * wait, wait)
                self.stats.next_retry_in = round(wait, 1)
                self._publish()
                self.store.log("warning", "realtime",
                               f"stream dropped ({self.stats.last_error}); reconnecting in "
                               f"{wait:.1f}s (attempt {self.stats.reconnects})")
                self._flush(force=True)
                if self._stop.wait(wait):
                    break
                backoff = min(backoff * 2, self.settings.reconnect_max_backoff)
            else:
                backoff = 1.0
        self._flush(force=True)
        self.stats.state = "stopped"
        self.stats.next_retry_in = None
        self._publish()

    def _on_status(self, kind: str, detail: dict[str, Any]) -> None:
        if kind == "subscribed":
            self.stats.state = "live"
            self.stats.connected_at = _now()
            self.stats.next_retry_in = None
            self.store.log("info", "realtime",
                           f"subscribed: {detail.get('symbols')} symbols, channels="
                           f"{detail.get('channels')}, feed={detail.get('feed')}")
        self._publish()

    # -- message handling --------------------------------------------------
    def _on_message(self, msg: dict[str, Any]) -> None:
        self.stats.messages += 1
        self.stats.last_message_at = _now()
        kind = msg.get("T")

        if kind == "error":
            self.stats.last_error = str(msg.get("msg") or msg)
            self.store.log("error", "realtime", f"stream error: {self.stats.last_error}")
            self._publish()
            return
        if kind in ("success", "subscription"):
            return

        symbol = (msg.get("S") or "").upper()
        if not symbol:
            return

        if kind == "b":                                   # minute bar
            ts = _parse_ts(msg.get("t"))
            if ts is None:
                return
            bar = Bar(ts, _f(msg.get("o")), _f(msg.get("h")), _f(msg.get("l")),
                      _f(msg.get("c")), _f(msg.get("v")))
            self._accept_bar(symbol, bar)
        elif kind == "t" and self.settings.store_raw_ticks:
            self._store_tick(symbol, msg)
        elif kind == "q" and self.settings.store_raw_ticks:
            self._store_quote(symbol, msg)

        now = time.time()
        if now - self._last_flush >= FLUSH_SECONDS:
            self._flush()
        if now - self._last_derive >= DERIVE_SECONDS:
            self._derive()

    def _accept_bar(self, symbol: str, bar: Bar) -> None:
        prev = self._last_ts.get(symbol)
        if prev is not None:
            if bar.ts <= prev:
                self.stats.duplicates += 1
                return
            gap = (bar.ts - prev).total_seconds()
            if gap > 60 and _in_regular_session(bar.ts) and _in_regular_session(prev):
                self.stats.gaps += 1
                missing = int(gap // 60) - 1
                self.store.log("warning", "realtime",
                               f"{symbol}: {missing} minute bar(s) missing between "
                               f"{prev:%H:%M} and {bar.ts:%H:%M}. Aggregates spanning the "
                               "gap understate volume.", symbol, "1m")
                self.store.flag(symbol, "1m", "stream_gap", "warning",
                                f"{missing} minute bar(s) missing during the regular "
                                f"session at {bar.ts:%Y-%m-%d %H:%M}")
        self._last_ts[symbol] = bar.ts
        with self._lock:
            self._buffer.setdefault(symbol, []).append(bar)
        self.stats.last_bar_at = bar.ts.isoformat()

    def _store_tick(self, symbol: str, msg: dict[str, Any]) -> None:
        sid = self.store.symbol_id(symbol)
        ts = _parse_ts(msg.get("t"))
        if ts is None:
            return
        self.store.execute(
            "INSERT INTO raw_trades(symbol_id,ts,price,size,exchange,tape,conditions,"
            "trade_id,provider,feed) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, int(ts.replace(tzinfo=dt.timezone.utc).timestamp() * 1_000_000),
             _f(msg.get("p")), _f(msg.get("s")), msg.get("x"), msg.get("z"),
             json.dumps(msg.get("c") or []), str(msg.get("i") or ""),
             self.stats.provider, self.stats.feed))

    def _store_quote(self, symbol: str, msg: dict[str, Any]) -> None:
        sid = self.store.symbol_id(symbol)
        ts = _parse_ts(msg.get("t"))
        if ts is None:
            return
        self.store.execute(
            "INSERT INTO raw_quotes(symbol_id,ts,bid,bid_size,bid_exchange,ask,ask_size,"
            "ask_exchange,provider,feed) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, int(ts.replace(tzinfo=dt.timezone.utc).timestamp() * 1_000_000),
             _f(msg.get("bp")), _f(msg.get("bs")), msg.get("bx"), _f(msg.get("ap")),
             _f(msg.get("as")), msg.get("ax"), self.stats.provider, self.stats.feed))

    # -- persistence -------------------------------------------------------
    def _flush(self, force: bool = False) -> int:
        with self._lock:
            batch = self._buffer
            self._buffer = {}
        self._last_flush = time.time()
        written = 0
        for symbol, bars in batch.items():
            if not bars:
                continue
            res = self.store.upsert_bars(symbol, "1m", bars, self.stats.provider or "stream",
                                         self.stats.feed, adjusted=False,
                                         allow_provider_override=True)
            written += res["rows"]
        if written:
            self.stats.bars_written += written
            self._publish()
        return written

    def _derive(self) -> None:
        """Roll 1m up to 5m/15m/1h and refresh today's daily bar."""
        self._last_derive = time.time()
        today = dt.date.today()
        for symbol in self._symbols:
            base = self.store.bars(symbol, "1m", start=today)
            if len(base) == 0:
                continue
            bars = list(base)
            for tf in ("5m", "15m", "1h"):
                rolled = aggregate_bars(bars, BAR_SECONDS[tf])
                self.store.upsert_bars(symbol, tf, rolled,
                                       self.stats.provider or "stream", self.stats.feed,
                                       adjusted=False, allow_provider_override=True)
            session = Bar(dt.datetime.combine(today, dt.time(16, 0)),
                          bars[0].open, max(b.high for b in bars),
                          min(b.low for b in bars), bars[-1].close,
                          sum(b.volume for b in bars))
            # The in-progress session bar is intraday-derived and unadjusted; it is
            # replaced by the vendor's official daily bar at the next backfill.
            self.store.upsert_bars(symbol, "1d", [session],
                                   f"{self.stats.provider or 'stream'}-intraday",
                                   self.stats.feed, adjusted=False,
                                   allow_provider_override=True)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_ts(raw: Any) -> dt.datetime | None:
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _in_regular_session(ts: dt.datetime) -> bool:
    return (cal.is_trading_day(ts.date())
            and cal.SESSION_OPEN <= ts.time() < cal.SESSION_CLOSE)


REALTIME = RealtimeIngestor()
