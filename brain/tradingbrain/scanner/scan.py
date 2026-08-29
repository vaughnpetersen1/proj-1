"""Daily setup scanner and stock ranking model.

The composite score is fully decomposed in the output: every candidate carries
the raw measurement, the 0-100 sub-score and the weight for each component, so
"NVDA 94/100" can always be unpacked into the eight numbers that produced it.

The weights are configurable and explicitly NOT asserted to be optimal. The
brief's suggested weights are the defaults; ``research.hypothesis`` can test
alternative weighting schemes out of sample.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Iterable

import numpy as np

from ..data.panel import Panel, build_panel
from ..data.providers.registry import HUB, DataHub
from ..provenance import Claim, DataOrigin, EvidenceClass
from ..regime.classifier import MarketRegime, classify_market
from ..sectors.ranking import SectorRanker, rank_sectors, rank_themes
from ..strategy.evaluator import Evaluator, SymbolContext
from ..strategy.library import get_strategy
from ..strategy.spec import StrategySpec

#: The weighting scheme suggested in the build brief. A starting point.
DEFAULT_WEIGHTS: dict[str, float] = {
    "market_regime": 0.15,
    "sector_strength": 0.15,
    "theme_strength": 0.10,
    "relative_strength": 0.15,
    "prior_momentum": 0.10,
    "consolidation_quality": 0.15,
    "volume_pattern": 0.10,
    "breakout_quality": 0.10,
}


@dataclasses.dataclass
class ScanConfig:
    strategy_key: str = "sar_v1_1_adr_stop"
    spec: StrategySpec | None = None
    as_of: dt.date | None = None
    include_pre_breakout: bool = True      # setups coiled but not yet triggered
    pre_breakout_max_pct_below: float = 5.0
    weights: dict[str, float] = dataclasses.field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    limit: int = 50
    symbols: list[str] | None = None
    require_market_filter: bool = False    # scan regardless; the score reflects regime

    def resolved_spec(self) -> StrategySpec:
        return self.spec or get_strategy(self.strategy_key)


@dataclasses.dataclass
class ScanCandidate:
    symbol: str
    date: str
    state: str                     # "breakout" | "approaching"
    score: float
    components: dict[str, dict[str, Any]]
    setup: dict[str, Any]
    sector: str | None
    themes: list[str]
    close: float
    breakout_level: float | None
    pct_to_breakout: float | None
    suggested_stop: float | None
    risk_pct: float | None
    reasons_failed: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _clip100(x: float) -> float:
    return float(np.clip(x, 0.0, 100.0))


def _score_components(*, regime: MarketRegime, sector_pct: float | None,
                      theme_pct: float | None, rs_pct: float | None,
                      snap: dict[str, Any], state: str) -> dict[str, dict[str, Any]]:
    """Each component returns raw measurement, 0-100 sub-score and its rationale."""
    comps: dict[str, dict[str, Any]] = {}

    comps["market_regime"] = {
        "raw": regime.strength,
        "score": _clip100(regime.strength if np.isfinite(regime.strength) else 50.0),
        "note": f"regime {regime.label}, {regime.volatility_label} volatility; "
                f"index MA filter {'passes' if regime.filter_pass else 'FAILS'}",
    }
    comps["sector_strength"] = {
        "raw": sector_pct,
        "score": _clip100(sector_pct if sector_pct is not None and np.isfinite(sector_pct) else 50.0),
        "note": "percentile rank of the symbol's sector among all sectors",
    }
    comps["theme_strength"] = {
        "raw": theme_pct,
        "score": _clip100(theme_pct if theme_pct is not None and np.isfinite(theme_pct) else 50.0),
        "note": "best percentile among the themes this symbol belongs to; 50 when it "
                "belongs to none (neutral, not penalised)",
    }
    comps["relative_strength"] = {
        "raw": rs_pct,
        "score": _clip100(rs_pct if rs_pct is not None and np.isfinite(rs_pct) else 50.0),
        "note": "63-bar return percentile against the scanned universe",
    }

    pm = snap.get("prior_move_pct")
    # 30% -> 50 points, 100% -> 100 points, linear, floored at 0
    pm_score = _clip100(((pm or 0.0) - 10.0) / 90.0 * 100.0)
    comps["prior_momentum"] = {
        "raw": pm, "score": pm_score,
        "note": "prior advance scaled 10%->0 through 100%->100",
    }

    q = snap.get("base_quality")
    comps["consolidation_quality"] = {
        "raw": q, "score": _clip100((q or 0.0) * 100.0),
        "note": "composite of base depth, range contraction, volume contraction, "
                "higher-low structure and 20 SMA support",
    }

    vc = snap.get("base_volume_contraction")
    # 0.5 (halved) -> 100 ; 1.0 (flat) -> 50 ; 1.5 (expanding) -> 0
    vscore = _clip100((1.5 - (vc if vc is not None else 1.0)) / 1.0 * 100.0)
    comps["volume_pattern"] = {
        "raw": vc, "score": vscore,
        "note": "second-half vs first-half base volume; below 1.0 means volume dried up",
    }

    rv = snap.get("rvol")
    if state == "breakout":
        bscore = _clip100(((rv or 1.0) - 0.8) / 2.2 * 100.0)
        note = "breakout-day relative volume, 0.8x->0 through 3.0x->100"
    else:
        # not yet triggered: score proximity to the trigger instead
        near = snap.get("pct_to_breakout")
        bscore = _clip100(100.0 - abs(near or 10.0) * 12.0)
        note = "not yet triggered; scored on proximity to the breakout level"
    comps["breakout_quality"] = {"raw": rv, "score": bscore, "note": note}
    return comps


def run_scan(cfg: ScanConfig = ScanConfig(), hub: DataHub = HUB,
             panel: Panel | None = None) -> dict[str, Any]:
    spec = cfg.resolved_spec()
    weights = {k: v for k, v in cfg.weights.items() if k in DEFAULT_WEIGHTS}
    tw = sum(weights.values()) or 1.0
    weights = {k: v / tw for k, v in weights.items()}

    panel = panel if panel is not None else build_panel(cfg.symbols, hub)
    as_of = cfg.as_of or panel.dates[-1]
    row = panel.row(as_of)
    if row < 0:
        return {"ok": False, "reason": f"no market data on or before {as_of}"}

    regime = classify_market(as_of, hub, with_breadth=True)
    sect = rank_sectors(as_of, hub, panel)
    them = rank_themes(as_of, hub, panel)
    n_sect = len(sect["ranks"]) or 1
    sector_pct = {g["name"]: 100.0 * (n_sect - g["rank"]) / max(n_sect - 1, 1)
                  for g in sect["ranks"]}
    n_them = len(them["ranks"]) or 1
    theme_pct = {g["name"]: 100.0 * (n_them - g["rank"]) / max(n_them - 1, 1)
                 for g in them["ranks"]}
    theme_members = hub.themes()
    sector_map = hub.sector_map()

    ranker = SectorRanker(hub, panel)
    rs_row = ranker._rs_percentile(spec.setup.rs_lookback)[row]

    ev = Evaluator(spec)
    candidates: list[ScanCandidate] = []
    scanned = 0
    for sym in panel.symbols:
        try:
            ser = hub.daily(sym, end=as_of)
        except Exception:  # noqa: BLE001
            continue
        if len(ser) < 80 or ser.ts[-1].date() != as_of:
            continue
        scanned += 1
        ctx = SymbolContext(ser, spec)
        i = len(ser) - 1
        j = panel.col(sym)
        rs = float(rs_row[j]) if np.isfinite(rs_row[j]) else None

        sector = sector_map.get(sym)
        s_pct = sector_pct.get(sector) if sector else None
        my_themes = [t for t, mem in theme_members.items() if sym in mem]
        t_pct = max((theme_pct.get(t, 0.0) for t in my_themes), default=None)

        state = "breakout" if ctx.breakouts[i] else "approaching"
        verdict = ev.evaluate(ctx, i, require_breakout=(state == "breakout"),
                              rs_percentile=rs, sector_percentile=s_pct,
                              collect_all_reasons=True)
        snap = dict(verdict.snapshot)

        base = ctx.cons.at(i - 1 if state == "breakout" else i)
        level = float(base.high) if base else None
        close = float(ser.close[i])
        pct_to = ((level / close - 1.0) * 100.0) if level else None
        snap["pct_to_breakout"] = pct_to

        if state == "approaching":
            if not cfg.include_pre_breakout:
                continue
            if base is None or not base.qualifies:
                continue
            if pct_to is None or not (-0.5 <= pct_to <= cfg.pre_breakout_max_pct_below):
                continue
            # a pre-breakout candidate must still satisfy everything except the trigger
            blocking = [r for r, c in zip(verdict.reasons_failed, verdict.fail_categories)
                        if c != "no breakout"]
            if blocking:
                continue
        elif not verdict.qualifies:
            continue

        comps = _score_components(regime=regime, sector_pct=s_pct, theme_pct=t_pct,
                                  rs_pct=rs, snap=snap, state=state)
        score = sum(weights[k] * comps[k]["score"] for k in weights)
        for k in comps:
            comps[k]["weight"] = round(weights.get(k, 0.0), 4)
            comps[k]["contribution"] = round(weights.get(k, 0.0) * comps[k]["score"], 2)

        stop = float(ser.low[i]) if state == "breakout" else (
            float(base.low) if base else None)
        risk_pct = ((close - stop) / close * 100.0) if stop and close else None
        candidates.append(ScanCandidate(
            symbol=sym, date=as_of.isoformat(), state=state, score=round(score, 1),
            components=comps, setup=snap, sector=sector, themes=my_themes,
            close=round(close, 4), breakout_level=round(level, 4) if level else None,
            pct_to_breakout=round(pct_to, 2) if pct_to is not None else None,
            suggested_stop=round(stop, 4) if stop else None,
            risk_pct=round(risk_pct, 2) if risk_pct else None,
            reasons_failed=verdict.reasons_failed if state == "approaching" else []))

    candidates.sort(key=lambda c: -c.score)
    top = candidates[:cfg.limit]
    origin = panel.origin
    return {
        "ok": True,
        "as_of": as_of.isoformat(),
        "strategy": f"{spec.name} v{spec.version}",
        "weights": weights,
        "symbols_scanned": scanned,
        "candidates_found": len(candidates),
        "breakouts_today": sum(1 for c in candidates if c.state == "breakout"),
        "approaching": sum(1 for c in candidates if c.state == "approaching"),
        "market_regime": regime.to_dict(),
        "leading_sectors": [{"name": g["name"], "score": g["score"], "rank": g["rank"]}
                            for g in sect["ranks"][:5]],
        "leading_themes": [{"name": g["name"], "score": g["score"], "rank": g["rank"]}
                           for g in them["ranks"][:5]],
        "results": [c.to_dict() for c in top],
        "data_origin": origin,
        "data_provider": panel.provider,
        "claim": Claim(
            statement=(f"{len(candidates)} symbols of {scanned} scanned matched the "
                       f"'{spec.name} v{spec.version}' setup definition on "
                       f"{as_of.isoformat()} ({sum(1 for c in candidates if c.state=='breakout')} "
                       "triggered, the rest approaching)."),
            evidence=EvidenceClass.LIVE_MARKET_OBSERVATION,
            value=len(candidates), sample_size=scanned, period=as_of.isoformat(),
            data_source=panel.provider,
            data_origin=DataOrigin(origin) if origin in DataOrigin.__members__
            else DataOrigin.UNKNOWN,
            methodology="Same Evaluator the backtester uses, applied to the latest bar. "
                        "Ranking weights are configurable and unvalidated.",
            caveats=["A high score means the setup matches the definition well. It is not "
                     "a probability, an expected return, or a recommendation.",
                     "Scores are comparable only within one scan run."],
        ).to_dict(),
    }
