"""Ingestion service: the only component that talks to a market-data vendor.

Everything else reads the database. That inversion is the point of this module:
a backtest, a scan or a chart must never trigger a vendor call, because that
makes cost proportional to curiosity and makes results depend on what the API
returned that afternoon.

Cost control is structural, not advisory:
  * `missing_ranges` is computed against the exchange calendar, so already-held
    bars are never re-requested and holidays are not mistaken for gaps.
  * Requests are batched across symbols where the provider supports it.
  * Incremental sync asks only for [last_held_bar + 1 trading day, today].
  * Every call is recorded in `api_usage` so the bill is visible before it lands.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import time
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from ..config import SETTINGS, Settings
from ..data.providers.base import (CorporateActionsProvider, HistoricalDataProvider,
                                   IntradayDataProvider, OptionsDataProvider,
                                   Provider, ProviderUnavailable, SymbolUniverseProvider)
from ..data.quality import validate
from ..data.types import Bar, PriceSeries, Timeframe
from ..marketstore import calendar as cal
from ..provenance import DataOrigin
from ..marketstore.store import MARKET, MarketStore

BAR_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


# ---------------------------------------------------------------------------

def aggregate_bars(bars: Sequence[Bar], seconds: int,
                   anchor: dt.time = dt.time(9, 30)) -> list[Bar]:
    """Roll finer bars up into `seconds`-wide bars.

    Buckets are anchored to the session open rather than to midnight, so a 5m
    bar covers 09:30-09:35 like a real one and not 09:30-09:34 with a stray
    fragment. Volume sums; open is the first open; close the last close.
    """
    out: list[Bar] = []
    bucket: list[Bar] = []
    bucket_start: dt.datetime | None = None
    for b in sorted(bars, key=lambda x: x.ts):
        day_anchor = dt.datetime.combine(b.ts.date(), anchor)
        offset = (b.ts - day_anchor).total_seconds()
        start = day_anchor + dt.timedelta(seconds=(offset // seconds) * seconds)
        if bucket_start is None or start != bucket_start:
            if bucket:
                out.append(_merge(bucket, bucket_start))
            bucket, bucket_start = [b], start
        else:
            bucket.append(b)
    if bucket:
        out.append(_merge(bucket, bucket_start))
    return out


def _merge(bars: list[Bar], ts: dt.datetime | None) -> Bar:
    return Bar(ts or bars[0].ts, bars[0].open, max(b.high for b in bars),
               min(b.low for b in bars), bars[-1].close, sum(b.volume for b in bars))


# ---------------------------------------------------------------------------

@dataclasses.dataclass
class SyncResult:
    symbol: str
    timeframe: str
    provider: str | None = None
    feed: str | None = None
    requested_ranges: int = 0
    rows_written: int = 0
    conflicts: int = 0
    skipped_up_to_date: bool = False
    flags: list[str] = dataclasses.field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class IngestionService:
    def __init__(self, store: MarketStore = MARKET, settings: Settings = SETTINGS,
                 provider_lookup: Callable[[str], Provider] | None = None) -> None:
        self.store = store
        self.settings = settings
        self._lookup = provider_lookup

    # -- provider selection ------------------------------------------------
    def _provider(self, name: str) -> Provider:
        if self._lookup is not None:
            return self._lookup(name)
        from ..data.providers.registry import get_provider
        return get_provider(name)

    def providers_in_order(self) -> list[str]:
        return list(self.settings.ingest_provider_order)

    def pick_provider(self, capability: str = "historical") -> tuple[Provider | None, list[str]]:
        """First configured, reachable provider offering `capability`.

        Returns the provider and the list of reasons the earlier ones were
        skipped, so a fallback is always explainable rather than mysterious.
        """
        from ..data.providers.registry import availability
        reasons: list[str] = []
        for name in self.providers_in_order():
            try:
                p = self._provider(name)
            except KeyError:
                reasons.append(f"{name}: not registered")
                continue
            if capability not in p.capabilities():
                reasons.append(f"{name}: no {capability} capability")
                continue
            ok, why = availability(p)
            if not ok:
                reasons.append(f"{name}: {why}")
                continue
            return p, reasons
        return None, reasons

    @staticmethod
    def feed_of(provider: Provider) -> str | None:
        return getattr(provider, "feed", None)

    # -- universe ----------------------------------------------------------
    def sync_universe(self, provider_name: str | None = None,
                      max_symbols: int | None = None) -> dict[str, Any]:
        """Refresh the tradeable symbol list so the screener is not a watchlist."""
        if provider_name:
            p = self._provider(provider_name)
        else:
            p, reasons = self.pick_provider("universe")
            if p is None:
                # A provider that can only enumerate what it can serve is still a
                # universe: better a small honest one than none.
                fallback, freasons = self.pick_provider("historical")
                if fallback is not None and hasattr(fallback, "symbols"):
                    rows = [{"symbol": s} for s in fallback.symbols()]  # type: ignore[attr-defined]
                    res = self.store.upsert_symbols(rows, source=fallback.name)
                    self._apply_static_sectors()
                    self.store.log("info", "universe",
                                   f"universe from {fallback.name} ({len(rows)} symbols); "
                                   "no dedicated universe provider is reachable")
                    return {**res, "source": fallback.name, "degraded": True,
                            "reasons": reasons + freasons}
                return {"ok": False, "reason": "no provider can supply a symbol universe",
                        "tried": reasons + freasons}
        rows = p.list_symbols(active_only=True)          # type: ignore[attr-defined]
        cap = max_symbols or self.settings.ingest_max_symbols
        if cap:
            rows = rows[:cap]
        res = self.store.upsert_symbols(rows, source=p.name)
        self._apply_static_sectors()
        self.store.log("info", "universe", f"universe synced from {p.name}: {res}")
        return {**res, "source": p.name, "degraded": False}

    def _apply_static_sectors(self) -> None:
        """Attach sector labels from whatever provider knows them.

        Alpaca's asset list carries no sector, so without this the sector engine
        has nothing to rank. Recorded with its source so a mapping from the
        generator is never mistaken for a vendor classification.
        """
        for name in self.providers_in_order():
            try:
                p = self._provider(name)
            except KeyError:
                continue
            if not hasattr(p, "sector_map"):
                continue
            mapping = p.sector_map()                      # type: ignore[attr-defined]
            if not mapping:
                continue
            for sym, sector in mapping.items():
                sid = self.store.symbol_id(sym, create=False)
                if sid is not None:
                    self.store.execute(
                        "UPDATE symbols SET sector=COALESCE(sector,?), "
                        "note=COALESCE(note,?) WHERE id=?",
                        (sector, f"sector from provider '{p.name}'", sid))
            return

    # -- history -----------------------------------------------------------
    def default_start(self, years: float | None = None) -> dt.date:
        y = years if years is not None else self.settings.history_years
        return dt.date.today() - dt.timedelta(days=int(y * 365.25))

    def backfill(self, symbols: Iterable[str], timeframe: str = "1d",
                 start: dt.date | None = None, end: dt.date | None = None,
                 provider_name: str | None = None,
                 force: bool = False) -> dict[str, Any]:
        """Fetch only what the store is missing, for each symbol."""
        tf = Timeframe(timeframe)
        syms = [s.upper() for s in symbols]
        if provider_name:
            provider: Provider | None = self._provider(provider_name)
            reasons: list[str] = []
        else:
            provider, reasons = self.pick_provider(
                "historical" if tf is Timeframe.D1 else "intraday")
        if provider is None:
            return {"ok": False, "reason": "no reachable provider for backfill",
                    "tried": reasons, "results": []}

        start = start or self.default_start()
        end = end or dt.date.today()
        feed = self.feed_of(provider)
        results: list[SyncResult] = []
        t0 = time.time()

        batched = getattr(provider, "bars_multi", None) if tf is Timeframe.D1 else None
        if batched is not None and not force:
            results = self._backfill_batched(provider, syms, tf, start, end, feed)
        else:
            for sym in syms:
                results.append(self._backfill_one(provider, sym, tf, start, end, feed, force))

        total_rows = sum(r.rows_written for r in results)
        self.store.log("info", "backfill",
                       f"{provider.name}/{feed or '-'} {tf.value}: {len(syms)} symbols, "
                       f"{total_rows} rows in {time.time() - t0:.1f}s")
        return {
            "ok": True, "provider": provider.name, "feed": feed,
            "feed_note": getattr(provider, "feed_description", lambda: None)(),
            "timeframe": tf.value, "symbols": len(syms),
            "rows_written": total_rows,
            "conflicts": sum(r.conflicts for r in results),
            "up_to_date": sum(1 for r in results if r.skipped_up_to_date),
            "errors": [r.to_dict() for r in results if r.error],
            "seconds": round(time.time() - t0, 2),
            "fallback_reasons": reasons,
            "results": [r.to_dict() for r in results],
        }

    def _needed_ranges(self, symbol: str, tf: Timeframe, start: dt.date,
                       end: dt.date, force: bool) -> list[tuple[dt.date, dt.date]]:
        if force:
            return [(start, end)]
        if tf is Timeframe.D1:
            return self.store.missing_daily_ranges(symbol, start, end)
        cov = self.store.coverage(symbol, tf.value)
        if not cov["rows"]:
            return [(start, end)]
        last = dt.date.fromisoformat(cov["last"])
        if last >= end:
            return []
        return [(cal.next_trading_day(last), end)]

    def _backfill_batched(self, provider: Provider, syms: list[str], tf: Timeframe,
                          start: dt.date, end: dt.date,
                          feed: str | None) -> list[SyncResult]:
        """Group symbols that need the same window into one vendor request."""
        need: dict[tuple[dt.date, dt.date], list[str]] = {}
        results: dict[str, SyncResult] = {}
        for sym in syms:
            r = SyncResult(sym, tf.value, provider.name, feed)
            ranges = self._needed_ranges(sym, tf, start, end, force=False)
            if not ranges:
                r.skipped_up_to_date = True
                results[sym] = r
                continue
            window = (min(a for a, _ in ranges), max(b for _, b in ranges))
            r.requested_ranges = len(ranges)
            results[sym] = r
            need.setdefault(window, []).append(sym)

        for (lo, hi), group in need.items():
            try:
                fetched = provider.bars_multi(group, tf, lo, hi)   # type: ignore[attr-defined]
            except ProviderUnavailable as exc:
                for sym in group:
                    results[sym].error = str(exc)
                    self.store.mark_sync_error(sym, tf.value, str(exc), provider.name)
                continue
            for sym in group:
                bars = fetched.get(sym, [])
                if not bars:
                    results[sym].error = "provider returned no bars for the requested window"
                    self.store.mark_sync_error(sym, tf.value, results[sym].error, provider.name)
                    continue
                self._store_and_validate(sym, tf, bars, provider, feed, results[sym])
        return list(results.values())

    def _backfill_one(self, provider: Provider, symbol: str, tf: Timeframe,
                      start: dt.date, end: dt.date, feed: str | None,
                      force: bool) -> SyncResult:
        r = SyncResult(symbol, tf.value, provider.name, feed)
        ranges = self._needed_ranges(symbol, tf, start, end, force)
        if not ranges:
            r.skipped_up_to_date = True
            return r
        r.requested_ranges = len(ranges)
        collected: list[Bar] = []
        for lo, hi in ranges:
            try:
                if tf is Timeframe.D1:
                    series = provider.daily(symbol, lo, hi)        # type: ignore[attr-defined]
                else:
                    series = provider.intraday(symbol, tf, lo, hi)  # type: ignore[attr-defined]
                collected.extend(list(series))
            except (ProviderUnavailable, KeyError, ValueError) as exc:
                r.error = str(exc)
                self.store.mark_sync_error(symbol, tf.value, str(exc), provider.name)
                return r
        if not collected:
            r.error = "provider returned no bars"
            self.store.mark_sync_error(symbol, tf.value, r.error, provider.name)
            return r
        self._store_and_validate(symbol, tf, collected, provider, feed, r)
        return r

    def _store_and_validate(self, symbol: str, tf: Timeframe, bars: list[Bar],
                            provider: Provider, feed: str | None,
                            r: SyncResult) -> None:
        origin = (DataOrigin.SYNTHETIC if getattr(provider, "synthetic", False)
                  else DataOrigin.REAL)
        series = PriceSeries.from_bars(symbol, tf, bars, provider=provider.name,
                                       origin=origin, adjusted=True)
        report = validate(series)
        blocking = [i for i in report.issues if i.severity == "error"]
        for issue in report.issues:
            if issue.severity in ("error", "warning"):
                self.store.flag(symbol, tf.value, issue.code, issue.severity,
                                issue.detail, issue.sample)
                r.flags.append(issue.code)
        if blocking:
            r.error = ("rejected on ingest: " +
                       "; ".join(f"{i.code} ({i.count})" for i in blocking))
            self.store.mark_sync_error(symbol, tf.value, r.error, provider.name)
            self.store.log("error", "validation", r.error, symbol, tf.value)
            return
        res = self.store.upsert_bars(symbol, tf.value, bars, provider.name, feed,
                                     adjusted=True)
        r.rows_written = res["rows"]
        r.conflicts = res["conflicts"]
        if tf is Timeframe.D1:
            self._flag_calendar_gaps(symbol, series)

    def _flag_calendar_gaps(self, symbol: str, series: PriceSeries) -> None:
        if len(series) < 5:
            return
        have = {t.date() for t in series.ts}
        expected = cal.trading_days(series.ts[0].date(), series.ts[-1].date())
        missing = [d for d in expected if d not in have]
        if not missing:
            return
        pct = 100.0 * len(missing) / max(len(expected), 1)
        severity = "error" if pct > 5 else "warning"
        self.store.flag(
            symbol, "1d", "missing_trading_days", severity,
            f"{len(missing)} of {len(expected)} expected trading days are absent "
            f"({pct:.1f}%). Weekends and exchange holidays are already excluded.",
            [d.isoformat() for d in missing[:10]])

    # -- incremental -------------------------------------------------------
    def incremental_sync(self, timeframe: str = "1d", symbols: Iterable[str] | None = None,
                         provider_name: str | None = None) -> dict[str, Any]:
        """Ask only for what happened since the last stored bar."""
        syms = [s.upper() for s in (symbols or self.store.symbol_list())]
        if not syms:
            return {"ok": False, "reason": "no symbols in the universe; sync it first"}
        today = dt.date.today()
        plan: list[str] = []
        for sym in syms:
            cov = self.store.coverage(sym, timeframe)
            if not cov["rows"]:
                plan.append(sym)
                continue
            last = dt.date.fromisoformat(cov["last"])
            if cal.next_trading_day(last) <= today:
                plan.append(sym)
        if not plan:
            return {"ok": True, "up_to_date": len(syms), "requested": 0,
                    "note": "every tracked symbol already holds the latest session"}
        res = self.backfill(plan, timeframe, provider_name=provider_name)
        res["requested"] = len(plan)
        res["up_to_date"] = len(syms) - len(plan)
        return res

    def derive_higher_timeframes(self, symbol: str,
                                 targets: Sequence[str] = ("5m", "15m", "1h")) -> dict[str, int]:
        """Build 5m/15m/1h from stored 1m bars instead of buying them again."""
        base = self.store.bars(symbol, "1m")
        if len(base) == 0:
            return {t: 0 for t in targets}
        out: dict[str, int] = {}
        provider = base.provider.split(":")[-1]
        feed = next((w.split("=", 1)[1] for w in base.warnings if w.startswith("feed=")), None)
        for tf in targets:
            rolled = aggregate_bars(list(base), BAR_SECONDS[tf])
            res = self.store.upsert_bars(symbol, tf, rolled, f"{provider}", feed,
                                         adjusted=base.adjusted,
                                         allow_provider_override=True)
            out[tf] = res["rows"]
        return out

    # -- corporate actions / options --------------------------------------
    def sync_corporate_actions(self, symbols: Iterable[str],
                               start: dt.date | None = None) -> dict[str, Any]:
        p, reasons = self.pick_provider("corporate_actions")
        if p is None:
            return {"ok": False, "reason": "no corporate-actions provider is reachable",
                    "tried": reasons,
                    "impact": ("Without corporate actions the store cannot verify that "
                               "vendor adjustment is correct, and split artefacts will be "
                               "flagged as suspicious price jumps instead of explained.")}
        total = 0
        errors = []
        for sym in symbols:
            try:
                actions = p.corporate_actions(sym, start)      # type: ignore[attr-defined]
                total += self.store.upsert_corporate_actions(sym, actions, p.name)
            except ProviderUnavailable as exc:
                errors.append({"symbol": sym, "error": str(exc)})
        return {"ok": True, "provider": p.name, "rows": total, "errors": errors}

    def sync_options(self, underlying: str,
                     expiration: dt.date | None = None) -> dict[str, Any]:
        p, reasons = self.pick_provider("options")
        if p is None:
            return {"ok": False, "reason": "no options provider is reachable",
                    "tried": reasons}
        contracts = p.chain(underlying, expiration)            # type: ignore[attr-defined]
        as_of = dt.date.today().isoformat()
        n = self.store.upsert_options(underlying, contracts, p.name,
                                      self.feed_of(p), as_of)
        return {"ok": True, "provider": p.name, "contracts": n, "as_of": as_of}

    # -- verification ------------------------------------------------------
    def validate_store(self, timeframe: str = "1d",
                       symbols: Iterable[str] | None = None) -> dict[str, Any]:
        """Re-run every quality check over what is already stored."""
        syms = [s.upper() for s in (symbols or self.store.symbol_list())]
        issues: dict[str, int] = {}
        checked = 0
        for sym in syms:
            series = self.store.bars(sym, timeframe)
            if len(series) == 0:
                continue
            checked += 1
            for issue in validate(series).issues:
                if issue.severity == "info":
                    continue
                issues[issue.code] = issues.get(issue.code, 0) + 1
                self.store.flag(sym, timeframe, issue.code, issue.severity,
                                issue.detail, issue.sample)
            if timeframe == "1d":
                self._flag_calendar_gaps(sym, series)
        return {"symbols_checked": checked, "issue_counts": issues,
                "unresolved_flags": len(self.store.flags())}


INGEST = IngestionService()
