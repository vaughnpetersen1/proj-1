"""Alpaca market-data provider.

Implements the historical, realtime, corporate-actions, options, quote and
universe protocols against Alpaca's v2 data API and v2 streaming endpoint.

FEED HONESTY
------------
Alpaca's free data tier serves the **IEX** feed. IEX is a single exchange and
carries a small single-digit percentage of consolidated US volume. A scanner
that treats IEX volume as market volume will mis-rank relative volume, miss
breakouts, and produce liquidity filters that are simply wrong. So this adapter
never reports "real-time market data" unqualified: ``feed_label()`` states the
feed, every bar row stores it, and the UI surfaces it. Set ``ALPACA_DATA_FEED=sip``
with a paid subscription for consolidated data.

CREDENTIALS
-----------
Read from the environment by the backend only. They are never returned by any
API route -- ``Settings.redacted()`` replaces them with "<set>", and the data
control centre reports presence, never value.

NOTE ON VERIFICATION: this environment's egress proxy denies HTTPS to
data.alpaca.markets, so these calls could not be exercised against the live API.
The request shapes follow Alpaca's documented v2 contract; the response parsing
is covered by ``tests/test_alpaca.py`` against recorded-shape fixtures, and every
failure path returns a specific ProviderUnavailable rather than a silent empty
result.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable

from ...provenance import DataOrigin
from ..types import Bar, PriceSeries, Timeframe
from .base import (CorporateActionsProvider, HistoricalDataProvider,
                   IntradayDataProvider, MarketDataProvider, OptionsDataProvider,
                   ProviderUnavailable, RealtimeMarketDataProvider,
                   SymbolUniverseProvider)
from .wsclient import WebSocket, WebSocketClosed

DATA_BASE = "https://data.alpaca.markets"
TRADING_BASE = "https://api.alpaca.markets"
PAPER_TRADING_BASE = "https://paper-api.alpaca.markets"
STREAM_BASE = "wss://stream.data.alpaca.markets/v2"

TIMEFRAME_PARAM = {
    Timeframe.M1: "1Min", Timeframe.M5: "5Min", Timeframe.M15: "15Min",
    Timeframe.H1: "1Hour", Timeframe.D1: "1Day", Timeframe.W1: "1Week",
}

FEED_DESCRIPTIONS = {
    "iex": ("IEX -- a single exchange carrying a low single-digit share of "
            "consolidated US volume. NOT full-market data: volume, relative volume "
            "and liquidity filters computed from it understate the real market."),
    "sip": ("SIP -- the consolidated tape covering all US exchanges. This is "
            "full-market data (requires a paid Alpaca subscription)."),
    "otc": "OTC -- over-the-counter venues only.",
    "delayed_sip": ("SIP delayed 15 minutes. Complete coverage, but never treat a "
                    "delayed print as a live quote."),
}

MAX_BARS_PER_PAGE = 10_000
#: Alpaca accepts many symbols per bars request. Batching is the single biggest
#: lever on API cost; one call for 100 symbols beats 100 calls for one.
SYMBOL_BATCH = 100


class AlpacaProvider(HistoricalDataProvider, IntradayDataProvider, MarketDataProvider,
                     RealtimeMarketDataProvider, CorporateActionsProvider,
                     OptionsDataProvider, SymbolUniverseProvider):
    name = "alpaca"
    synthetic = False

    def __init__(self, api_key: str | None = None, api_secret: str | None = None,
                 feed: str = "iex", paper: bool = True, timeout: float = 30.0,
                 usage_recorder: Callable[..., None] | None = None) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.feed = (feed or "iex").lower()
        self.paper = paper
        self.timeout = timeout
        self._usage = usage_recorder

    # -- identity ----------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        if not (self.api_key and self.api_secret):
            return False, ("ALPACA_API_KEY / ALPACA_API_SECRET not set (see .env.example). "
                           "Credentials are read by the backend only.")
        try:
            self._get(f"{DATA_BASE}/v2/stocks/bars/latest",
                      {"symbols": "SPY", "feed": self.feed}, endpoint="ping")
            return True, f"authenticated; feed={self.feed_label()}"
        except ProviderUnavailable as exc:
            return False, f"credentials set but {exc}"

    def feed_label(self) -> str:
        return self.feed.upper()

    def feed_description(self) -> str:
        return FEED_DESCRIPTIONS.get(self.feed, f"unrecognised feed {self.feed!r}")

    def is_full_market(self) -> bool:
        return self.feed in ("sip", "delayed_sip")

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d.update(feed=self.feed, feed_label=self.feed_label(),
                 feed_description=self.feed_description(),
                 full_market_coverage=self.is_full_market(),
                 credentials_present=bool(self.api_key and self.api_secret))
        return d

    # -- http --------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        if not (self.api_key and self.api_secret):
            raise ProviderUnavailable("Alpaca credentials are not configured")
        return {"APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
                "Accept": "application/json"}

    def _get(self, url: str, params: dict[str, Any] | None = None,
             endpoint: str = "") -> dict[str, Any]:
        q = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        full = url + ("?" + urllib.parse.urlencode(q) if q else "")
        req = urllib.request.Request(full, headers=self._headers())
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read() or b"{}")
            self._record(endpoint or url, ok=True, ms=(time.time() - t0) * 1000,
                         symbols=len(str(q.get("symbols", "")).split(",")) if q.get("symbols") else 1)
            return payload
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            msg = {401: "authentication rejected -- check ALPACA_API_KEY/SECRET",
                   403: (f"forbidden -- the '{self.feed}' feed is not included in this "
                         "subscription. Free keys are limited to feed=iex."),
                   429: "rate limited by Alpaca; back off and retry",
                   }.get(exc.code, f"HTTP {exc.code}")
            self._record(endpoint or url, ok=False, ms=(time.time() - t0) * 1000,
                         error=f"{msg}: {body}")
            raise ProviderUnavailable(f"{msg} ({body})") from exc
        except Exception as exc:                              # noqa: BLE001
            self._record(endpoint or url, ok=False, ms=(time.time() - t0) * 1000,
                         error=f"{type(exc).__name__}: {exc}")
            raise ProviderUnavailable(
                f"{type(exc).__name__} contacting {urllib.parse.urlparse(url).netloc}: {exc}"
            ) from exc

    def _record(self, endpoint: str, *, ok: bool, ms: float, rows: int = 0,
                symbols: int = 1, error: str | None = None) -> None:
        if self._usage:
            try:
                self._usage(provider=self.name, endpoint=endpoint, ok=ok, ms=ms,
                            rows=rows, symbols=symbols, error=error)
            except Exception:                                 # noqa: BLE001
                pass

    # -- bars --------------------------------------------------------------
    @staticmethod
    def _parse_ts(raw: str) -> dt.datetime:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)

    @staticmethod
    def _to_bar(b: dict[str, Any]) -> Bar:
        return Bar(AlpacaProvider._parse_ts(b["t"]), float(b["o"]), float(b["h"]),
                   float(b["l"]), float(b["c"]), float(b.get("v") or 0.0))

    def bars_multi(self, symbols: Iterable[str], timeframe: Timeframe,
                   start: dt.date | dt.datetime | None = None,
                   end: dt.date | dt.datetime | None = None,
                   adjustment: str = "all",
                   limit_per_request: int = MAX_BARS_PER_PAGE) -> dict[str, list[Bar]]:
        """Batched, paginated bar fetch. One request per page per symbol batch."""
        syms = [s.upper() for s in symbols]
        out: dict[str, list[Bar]] = {s: [] for s in syms}
        tf = TIMEFRAME_PARAM.get(timeframe)
        if tf is None:
            raise ProviderUnavailable(f"Alpaca has no timeframe mapping for {timeframe.value}")

        for i in range(0, len(syms), SYMBOL_BATCH):
            batch = syms[i:i + SYMBOL_BATCH]
            token: str | None = None
            pages = 0
            while True:
                params = {
                    "symbols": ",".join(batch), "timeframe": tf,
                    "adjustment": adjustment, "feed": self.feed,
                    "limit": limit_per_request, "sort": "asc",
                }
                if start is not None:
                    params["start"] = _iso(start, False)
                if end is not None:
                    params["end"] = _iso(end, True)
                if token:
                    params["page_token"] = token
                payload = self._get(f"{DATA_BASE}/v2/stocks/bars", params, endpoint="stocks/bars")
                rows = payload.get("bars") or {}
                for sym, bars in rows.items():
                    out.setdefault(sym.upper(), []).extend(
                        self._to_bar(b) for b in bars if b.get("c") is not None)
                token = payload.get("next_page_token")
                pages += 1
                if not token:
                    break
                if pages > 500:                       # guard against a runaway cursor
                    raise ProviderUnavailable(
                        "pagination exceeded 500 pages; refusing to keep spending calls")
        return out

    def daily(self, symbol: str, start: dt.date | None = None,
              end: dt.date | None = None) -> PriceSeries:
        bars = self.bars_multi([symbol], Timeframe.D1, start, end).get(symbol.upper(), [])
        if not bars:
            raise ProviderUnavailable(
                f"Alpaca returned no daily bars for {symbol} on feed={self.feed}")
        return PriceSeries.from_bars(symbol, Timeframe.D1, bars, provider=self.name,
                                     origin=DataOrigin.REAL, adjusted=True)

    def intraday(self, symbol: str, timeframe: Timeframe, start: dt.date | None = None,
                 end: dt.date | None = None) -> PriceSeries:
        bars = self.bars_multi([symbol], timeframe, start, end).get(symbol.upper(), [])
        if not bars:
            raise ProviderUnavailable(
                f"Alpaca returned no {timeframe.value} bars for {symbol} on feed={self.feed}")
        return PriceSeries.from_bars(symbol, timeframe, bars, provider=self.name,
                                     origin=DataOrigin.REAL, adjusted=True)

    # -- quotes / trades ---------------------------------------------------
    def quote(self, symbol: str) -> dict[str, Any]:
        payload = self._get(f"{DATA_BASE}/v2/stocks/{symbol.upper()}/quotes/latest",
                            {"feed": self.feed}, endpoint="quotes/latest")
        q = payload.get("quote") or {}
        return {"symbol": symbol.upper(), "bid": q.get("bp"), "bid_size": q.get("bs"),
                "ask": q.get("ap"), "ask_size": q.get("as"),
                "ts": q.get("t"), "feed": self.feed, "provider": self.name,
                "feed_note": self.feed_description()}

    def trades(self, symbol: str, start: dt.datetime, end: dt.datetime,
               limit: int = 10_000) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token = None
        while True:
            params = {"start": _iso(start, False), "end": _iso(end, True),
                      "limit": limit, "feed": self.feed, "page_token": token}
            payload = self._get(f"{DATA_BASE}/v2/stocks/{symbol.upper()}/trades",
                                params, endpoint="stocks/trades")
            out.extend(payload.get("trades") or [])
            token = payload.get("next_page_token")
            if not token or len(out) >= limit:
                break
        return out

    def quotes(self, symbol: str, start: dt.datetime, end: dt.datetime,
               limit: int = 10_000) -> list[dict[str, Any]]:
        payload = self._get(f"{DATA_BASE}/v2/stocks/{symbol.upper()}/quotes",
                            {"start": _iso(start, False), "end": _iso(end, True),
                             "limit": limit, "feed": self.feed}, endpoint="stocks/quotes")
        return payload.get("quotes") or []

    # -- corporate actions -------------------------------------------------
    _CA_TYPES = ("reverse_split", "forward_split", "cash_dividend", "stock_dividend",
                 "spin_off", "merger", "name_change", "symbol_change")

    def corporate_actions(self, symbol: str, start: dt.date | None = None,
                          end: dt.date | None = None) -> list[dict[str, Any]]:
        params = {"symbols": symbol.upper(), "types": ",".join(self._CA_TYPES),
                  "start": (start or dt.date(2000, 1, 1)).isoformat(),
                  "end": (end or dt.date.today()).isoformat(), "limit": 1000}
        payload = self._get(f"{DATA_BASE}/v1/corporate-actions", params,
                            endpoint="corporate-actions")
        actions = payload.get("corporate_actions") or {}
        out: list[dict[str, Any]] = []
        for kind, rows in actions.items():
            for r in rows or []:
                out.append(_normalise_corporate_action(kind, r))
        out.sort(key=lambda a: a.get("ex_date") or "")
        return out

    # -- options -----------------------------------------------------------
    def expirations(self, symbol: str) -> list[dt.date]:
        return sorted({dt.date.fromisoformat(c["expiration"])
                       for c in self.chain(symbol) if c.get("expiration")})

    def chain(self, symbol: str, expiration: dt.date | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": 1000, "feed": "indicative"}
        if expiration:
            params["expiration_date"] = expiration.isoformat()
        payload = self._get(f"{DATA_BASE}/v1beta1/options/snapshots/{symbol.upper()}",
                            params, endpoint="options/snapshots")
        snaps = payload.get("snapshots") or {}
        out: list[dict[str, Any]] = []
        for contract, snap in snaps.items():
            parsed = _parse_occ(contract)
            greeks = snap.get("greeks") or {}
            quote = snap.get("latestQuote") or {}
            trade = snap.get("latestTrade") or {}
            out.append({
                "contract": contract, "symbol": symbol.upper(),
                "type": parsed.get("type"), "strike": parsed.get("strike"),
                "expiration": parsed.get("expiration"),
                "bid": quote.get("bp"), "ask": quote.get("ap"),
                "last": trade.get("p"), "volume": (snap.get("dailyBar") or {}).get("v"),
                "open_interest": snap.get("openInterest"),
                "implied_volatility": snap.get("impliedVolatility"),
                "delta": greeks.get("delta"), "gamma": greeks.get("gamma"),
                "theta": greeks.get("theta"), "vega": greeks.get("vega"),
                "rho": greeks.get("rho"), "source": self.name,
            })
        return out

    # -- universe ----------------------------------------------------------
    def list_symbols(self, active_only: bool = True) -> list[dict[str, Any]]:
        base = PAPER_TRADING_BASE if self.paper else TRADING_BASE
        payload = self._get(f"{base}/v2/assets",
                            {"status": "active" if active_only else None,
                             "asset_class": "us_equity"}, endpoint="assets")
        rows = payload if isinstance(payload, list) else payload.get("assets", [])
        return [{
            "symbol": a.get("symbol"), "name": a.get("name"),
            "exchange": a.get("exchange"), "asset_class": a.get("class"),
            "active": 1 if a.get("status") == "active" else 0,
            "tradable": int(bool(a.get("tradable"))),
            "shortable": int(bool(a.get("shortable"))),
            "easy_to_borrow": int(bool(a.get("easy_to_borrow"))),
        } for a in rows if a.get("symbol")]

    # -- realtime ----------------------------------------------------------
    def stream_url(self) -> str:
        return f"{STREAM_BASE}/{self.feed}"

    def stream(self, symbols: list[str], on_message: Callable[[dict[str, Any]], None],
               channels: tuple[str, ...] = ("bars",),
               stop: threading.Event | None = None,
               heartbeat_seconds: float = 20.0,
               on_status: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        """Connect, authenticate, subscribe, and pump messages until ``stop``.

        Blocks. The reconnect loop lives in the ingestion service, not here: this
        method raises on disconnect so the caller can decide the backoff policy.
        """
        if not (self.api_key and self.api_secret):
            raise ProviderUnavailable("Alpaca credentials are not configured")
        syms = [s.upper() for s in symbols]
        ws = WebSocket(self.stream_url(), timeout=heartbeat_seconds * 2)
        try:
            hello = json.loads(ws.recv() or "[]")
            if on_status:
                on_status("connected", {"hello": hello, "feed": self.feed})
            ws.send_json({"action": "auth", "key": self.api_key, "secret": self.api_secret})
            auth = json.loads(ws.recv() or "[]")
            if not _has_message(auth, "authenticated"):
                raise ProviderUnavailable(f"Alpaca stream auth failed: {auth}")
            sub: dict[str, Any] = {"action": "subscribe"}
            for ch in channels:
                sub[ch] = syms
            ws.send_json(sub)
            if on_status:
                on_status("subscribed", {"symbols": len(syms), "channels": list(channels),
                                         "feed": self.feed})
            last_ping = last_rx = time.time()
            while not (stop and stop.is_set()):
                try:
                    raw = ws.recv()
                except TimeoutError:
                    # No bytes within the socket timeout. Not fatal on its own --
                    # a quiet symbol set is normal outside the open -- but if the
                    # peer stops answering pings we must not sit here forever.
                    raw = None
                    if time.time() - last_rx > heartbeat_seconds * 3:
                        raise WebSocketClosed(
                            1006, f"no data or pong for {heartbeat_seconds * 3:.0f}s")
                if raw is not None:
                    last_rx = time.time()
                if raw:
                    for msg in json.loads(raw):
                        on_message(msg)
                if time.time() - last_ping > heartbeat_seconds:
                    ws.ping()
                    last_ping = time.time()
        finally:
            ws.close()


def _has_message(payload: Any, needle: str) -> bool:
    if isinstance(payload, list):
        return any(_has_message(p, needle) for p in payload)
    if isinstance(payload, dict):
        return payload.get("msg") == needle or payload.get("T") == needle
    return False


def _iso(v: dt.date | dt.datetime, end_of_day: bool) -> str:
    if isinstance(v, dt.datetime):
        return v.replace(microsecond=0).isoformat() + "Z"
    t = dt.time(23, 59, 59) if end_of_day else dt.time(0, 0, 0)
    return dt.datetime.combine(v, t).isoformat() + "Z"


def _normalise_corporate_action(kind: str, r: dict[str, Any]) -> dict[str, Any]:
    """Map Alpaca's per-type payloads onto one shape the store understands."""
    ex = (r.get("ex_date") or r.get("effective_date") or r.get("process_date")
          or r.get("payable_date"))
    ratio = None
    if kind in ("forward_split", "reverse_split"):
        new, old = r.get("new_rate"), r.get("old_rate")
        try:
            ratio = float(new) / float(old) if new and old else None
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = None
    return {
        "action_type": kind,
        "ex_date": ex,
        "ratio": ratio,
        "cash_amount": _f(r.get("rate")) if kind == "cash_dividend" else None,
        "record_date": r.get("record_date"),
        "payable_date": r.get("payable_date"),
        "raw": r,
    }


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_occ(contract: str) -> dict[str, Any]:
    """AAPL240119C00150000 -> {expiration, type, strike}."""
    try:
        i = 0
        while i < len(contract) and not contract[i].isdigit():
            i += 1
        body = contract[i:]
        y, m, d = int(body[0:2]), int(body[2:4]), int(body[4:6])
        kind = "call" if body[6].upper() == "C" else "put"
        strike = int(body[7:15]) / 1000.0
        return {"expiration": dt.date(2000 + y, m, d).isoformat(),
                "type": kind, "strike": strike, "underlying": contract[:i]}
    except (ValueError, IndexError):
        return {"expiration": None, "type": None, "strike": None}
