"""Historical analog search -- the "statistical odds" engine.

Given a setup definition, find every historical bar that satisfied it and report
what happened *next*. The output is deliberately verbose about sample size,
period, definition of success and data origin, because a forward-return
distribution without those four things is not evidence.

Phrasing rule enforced here: never "63.4% chance"; always "63.4% of the 428
qualifying historical events were positive after 10 trading days".
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Iterable

import numpy as np

from ..data.panel import Panel, build_panel
from ..data.providers.registry import HUB, DataHub
from ..provenance import Claim, DataOrigin, EvidenceClass, proportion_statement
from ..strategy.evaluator import Evaluator, SymbolContext
from ..strategy.spec import StrategySpec
from .core import bootstrap_ci, describe, sample_size_verdict, wilson_interval

DEFAULT_HORIZONS = (1, 3, 5, 10, 20)


@dataclasses.dataclass
class SetupEvent:
    symbol: str
    date: str
    bar_index: int
    entry_ref: float          # next bar's open -- the realistic entry reference
    stop: float
    risk_per_share: float
    snapshot: dict[str, Any]
    forward_returns: dict[int, float] = dataclasses.field(default_factory=dict)
    mfe_r: float | None = None
    mae_r: float | None = None
    hit_target_first: bool | None = None
    bars_to_outcome: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["forward_returns"] = {str(k): v for k, v in self.forward_returns.items()}
        return d


@dataclasses.dataclass
class AnalogQuery:
    spec: StrategySpec
    start: dt.date | None = None
    end: dt.date | None = None
    symbols: list[str] | None = None
    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    target_r: float = 3.0
    max_forward_bars: int = 60
    apply_market_filter: bool = False
    exclude_symbol: str | None = None
    max_events: int = 20_000

    def describe(self) -> dict[str, Any]:
        return {
            "strategy": f"{self.spec.name} v{self.spec.version}",
            "setup": dataclasses.asdict(self.spec.setup),
            "entry": dataclasses.asdict(self.spec.entry),
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "horizons": list(self.horizons),
            "target_r": self.target_r,
            "market_filter_applied": self.apply_market_filter,
        }


def _market_ok_map(spec: StrategySpec, hub: DataHub) -> dict[dt.date, bool]:
    from ..indicators.core import sma
    mf = spec.market_filter
    maps: list[dict[dt.date, bool]] = []
    for sym in filter(None, (mf.symbol, mf.secondary_symbol)):
        try:
            ser = hub.daily(sym)
        except Exception:  # noqa: BLE001
            return {}
        f, s = sma(ser.close, mf.fast), sma(ser.close, mf.slow)
        ok = (f > s) & np.isfinite(f) & np.isfinite(s)
        maps.append({t.date(): bool(o) for t, o in zip(ser.ts, ok)})
    if not maps:
        return {}
    out: dict[dt.date, bool] = {}
    for d in set().union(*[set(m) for m in maps]):
        vals = [m[d] for m in maps if d in m]
        out[d] = all(vals) if mf.mode == "both" else any(vals)
    return out


def find_historical_setups(query: AnalogQuery, hub: DataHub = HUB,
                           panel: Panel | None = None) -> tuple[list[SetupEvent], dict[str, Any]]:
    spec = query.spec
    ev = Evaluator(spec)
    panel = panel if panel is not None else build_panel(
        query.symbols, hub, query.start, query.end)
    rs = (panel.rank_percentile(panel.returns(spec.setup.rs_lookback))
          if spec.setup.min_rs_percentile is not None else None)
    mkt = _market_ok_map(spec, hub) if query.apply_market_filter else {}

    events: list[SetupEvent] = []
    origins: set[str] = set()
    provider = "unknown"
    scanned_bars = 0
    for sym in panel.symbols:
        if query.exclude_symbol and sym == query.exclude_symbol.upper():
            continue
        try:
            ser = hub.daily(sym, query.start, query.end)
        except Exception:  # noqa: BLE001
            continue
        if len(ser) < 80:
            continue
        origins.add(ser.origin.value)
        provider = ser.provider
        ctx = SymbolContext(ser, spec)
        col = panel.col(sym) if sym in panel.symbols else None
        idx = np.flatnonzero(ctx.breakouts)
        for i in idx:
            i = int(i)
            scanned_bars += 1
            if i < 60 or i + 1 >= len(ser):
                continue
            day = ser.ts[i].date()
            if query.apply_market_filter and mkt and not mkt.get(day, False):
                continue
            rsv = None
            if rs is not None and col is not None:
                prow = panel.row(day)
                if prow >= 0:
                    v = rs[prow, col]
                    rsv = float(v) if np.isfinite(v) else None
            v = ev.evaluate(ctx, i, rs_percentile=rsv)
            if not v.qualifies:
                continue
            entry = float(ser.open[i + 1])
            stop = min(float(ser.low[i]), entry * 0.999)
            rps = entry - stop
            if rps <= 0:
                continue
            e = SetupEvent(symbol=sym, date=day.isoformat(), bar_index=i, entry_ref=entry,
                           stop=stop, risk_per_share=rps, snapshot=v.snapshot)
            _fill_forward(e, ser, i, query)
            events.append(e)
            if len(events) >= query.max_events:
                break
        if len(events) >= query.max_events:
            break

    meta = {
        "events": len(events),
        "symbols_scanned": len(panel.symbols),
        "breakout_bars_examined": scanned_bars,
        "data_origin": "SYNTHETIC" if "SYNTHETIC" in origins else (
            origins.pop() if len(origins) == 1 else "MIXED"),
        "data_provider": provider,
        "period": f"{panel.dates[0].isoformat()}..{panel.dates[-1].isoformat()}" if panel.dates else "",
    }
    return events, meta


def _fill_forward(e: SetupEvent, ser, i: int, q: AnalogQuery) -> None:
    """Forward outcome measured from the *next* open, the realistic entry point."""
    n = len(ser)
    entry = e.entry_ref
    for h in q.horizons:
        j = i + 1 + h
        if j < n:
            e.forward_returns[h] = float((ser.close[j] / entry - 1.0) * 100.0)
    end = min(n, i + 1 + q.max_forward_bars)
    if end <= i + 1:
        return
    highs = ser.high[i + 1:end]
    lows = ser.low[i + 1:end]
    e.mfe_r = float((np.max(highs) - entry) / e.risk_per_share)
    e.mae_r = float((np.min(lows) - entry) / e.risk_per_share)
    target = entry + q.target_r * e.risk_per_share
    for k in range(end - (i + 1)):
        # A bar containing both is resolved against the trade: stop first.
        if lows[k] <= e.stop:
            e.hit_target_first, e.bars_to_outcome = False, k + 1
            return
        if highs[k] >= target:
            e.hit_target_first, e.bars_to_outcome = True, k + 1
            return


def analog_statistics(events: list[SetupEvent], query: AnalogQuery,
                      meta: dict[str, Any]) -> dict[str, Any]:
    n = len(events)
    origin = meta.get("data_origin", "UNKNOWN")
    out: dict[str, Any] = {
        "query": query.describe(), "meta": meta, "n": n,
        "sample_verdict": sample_size_verdict(n),
        "horizons": {}, "excursions": {}, "path": {}, "claims": [],
    }
    if n == 0:
        out["claims"].append(Claim(
            statement="No historical events matched this setup definition over the "
                      "requested universe and period.",
            evidence=EvidenceClass.BACKTEST_RESULT,
            sample_size=0, period=meta.get("period"),
            data_source=meta.get("data_provider", "unknown"),
            data_origin=DataOrigin(origin) if origin in DataOrigin.__members__ else DataOrigin.UNKNOWN,
            methodology="Exhaustive scan of every bar in the universe against the setup rules.",
        ).to_dict())
        return out

    for h in query.horizons:
        vals = [e.forward_returns[h] for e in events if h in e.forward_returns]
        if not vals:
            continue
        arr = np.array(vals, float)
        pos = int((arr > 0).sum())
        prop = wilson_interval(pos, len(arr))
        mean_ci = bootstrap_ci(arr, np.mean, n_boot=2000, name=f"mean {h}-bar return")
        out["horizons"][str(h)] = {
            "n": len(arr),
            "pct_positive": round(100.0 * pos / len(arr), 2),
            "pct_positive_ci": [round(prop.ci_low * 100, 2), round(prop.ci_high * 100, 2)],
            "mean_return_pct": round(float(arr.mean()), 3),
            "mean_return_ci": [round(mean_ci.ci_low, 3), round(mean_ci.ci_high, 3)]
                              if mean_ci.ci_low is not None else None,
            "median_return_pct": round(float(np.median(arr)), 3),
            "distribution": describe(arr, f"{h}-bar forward return %"),
            "statement": proportion_statement(pos, len(arr), f"{h} trading days",
                                              "produced a positive return from the next open"),
        }
        out["claims"].append(Claim(
            statement=out["horizons"][str(h)]["statement"],
            evidence=EvidenceClass.BACKTEST_RESULT,
            value=out["horizons"][str(h)]["pct_positive"],
            sample_size=len(arr),
            period=meta.get("period"),
            definition_of_success="close above the next-open entry reference",
            data_source=meta.get("data_provider", "unknown"),
            data_origin=DataOrigin(origin) if origin in DataOrigin.__members__ else DataOrigin.UNKNOWN,
            confidence_interval=(round(prop.ci_low * 100, 2), round(prop.ci_high * 100, 2)),
            methodology="Wilson score interval on the proportion; percentile bootstrap on the mean.",
            caveats=["Overlapping events on correlated names are not independent, so the "
                     "effective sample is smaller than N."],
        ).to_dict())

    mfe = np.array([e.mfe_r for e in events if e.mfe_r is not None], float)
    mae = np.array([e.mae_r for e in events if e.mae_r is not None], float)
    if len(mfe):
        out["excursions"]["mfe_r"] = describe(mfe, "maximum favourable excursion (R)")
    if len(mae):
        out["excursions"]["mae_r"] = describe(mae, "maximum adverse excursion (R)")

    resolved = [e for e in events if e.hit_target_first is not None]
    hits = sum(1 for e in resolved if e.hit_target_first)
    if resolved:
        ci = wilson_interval(hits, len(resolved))
        bars = [e.bars_to_outcome for e in resolved if e.bars_to_outcome]
        out["path"] = {
            "target_r": query.target_r,
            "resolved": len(resolved),
            "unresolved": n - len(resolved),
            "pct_hit_target_first": round(100.0 * hits / len(resolved), 2),
            "pct_hit_target_first_ci": [round(ci.ci_low * 100, 2), round(ci.ci_high * 100, 2)],
            "pct_hit_stop_first": round(100.0 * (len(resolved) - hits) / len(resolved), 2),
            "median_bars_to_outcome": float(np.median(bars)) if bars else None,
            "expectancy_r_if_binary": round(
                (hits * query.target_r - (len(resolved) - hits) * 1.0) / len(resolved), 4),
            "statement": proportion_statement(
                hits, len(resolved), f"a {query.max_forward_bars}-bar window",
                f"reached +{query.target_r}R before touching the low-of-signal-day stop"),
            "caveat": ("Binary expectancy assumes every trade is held to +%.1fR or the "
                       "initial stop with no partials, no trailing and no time stop. It is "
                       "a property of the setup, not of any managed strategy."
                       % query.target_r),
        }
        out["claims"].append(Claim(
            statement=out["path"]["statement"],
            evidence=EvidenceClass.BACKTEST_RESULT,
            value=out["path"]["pct_hit_target_first"],
            sample_size=len(resolved), period=meta.get("period"),
            definition_of_success=f"high >= entry + {query.target_r}R before low <= stop",
            data_source=meta.get("data_provider", "unknown"),
            data_origin=DataOrigin(origin) if origin in DataOrigin.__members__ else DataOrigin.UNKNOWN,
            confidence_interval=(round(ci.ci_low * 100, 2), round(ci.ci_high * 100, 2)),
            methodology="Bar-by-bar path walk; a bar containing both levels resolves to the stop.",
        ).to_dict())

    out["examples"] = [e.to_dict() for e in events[:25]]
    return out
