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
    #: Read precomputed rows from strategy_features instead of recomputing every
    #: indicator per request. Falls back automatically when features are absent.
    use_precomputed_features: bool = True

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
    """Rank setups. Reads precomputed features when they exist.

    The slow path (recomputing every indicator for every symbol) is kept because
    it is the definition of correctness -- ``tests/test_market_data.py`` asserts
    the two agree -- but it is not what a user waiting on the screener should pay
    for.
    """
    if cfg.use_precomputed_features:
        fast = _scan_from_features(cfg, hub)
        if fast is not None:
            return fast
    return _scan_recompute(cfg, hub, panel)


def _scan_from_features(cfg: ScanConfig, hub: DataHub) -> dict[str, Any] | None:
    """Fast path: one SQL read, then scoring. No indicator recomputation."""
    import time as _time
    from ..marketstore.store import MARKET
    from ..screener.sql_screener import SqlScreener

    t0 = _time.time()
    spec = cfg.resolved_spec()
    screener = SqlScreener(MARKET)
    as_of = cfg.as_of or screener._latest_feature_date("1d")
    if as_of is None:
        return None

    themes = hub.themes()
    screen = screener.run({"min_price": spec.universe.min_price,
                           "min_avg_dollar_volume": spec.universe.min_dollar_volume},
                          as_of=as_of, limit=100_000, save=False, themes=themes)
    if not screen.get("ok") or not screen["results"]:
        return None

    weights = {k: v for k, v in cfg.weights.items() if k in DEFAULT_WEIGHTS}
    tw = sum(weights.values()) or 1.0
    weights = {k: v / tw for k, v in weights.items()}

    # Read the STORED regime. Recomputing it here would load every symbol's bars
    # on every scan -- exactly the per-request work this path exists to remove.
    regime = _stored_regime(MARKET, as_of) or classify_market(as_of, hub,
                                                              with_breadth=True)
    stored_sectors = MARKET.sector_history("sector", limit=40)
    stored_themes = MARKET.sector_history("theme", limit=40)
    sect_pct = screener._group_percentiles(as_of, "sector")
    theme_pct = screener._group_percentiles(as_of, "theme")

    candidates: list[ScanCandidate] = []
    for row in screen["results"]:
        triggered = bool(row.get("breakout_today"))
        state = "breakout" if triggered else "approaching"
        dist = row.get("breakout_distance_pct")
        if not triggered:
            if not cfg.include_pre_breakout:
                continue
            if not row.get("base_qualifies"):
                continue
            if dist is None or not (-0.5 <= dist <= cfg.pre_breakout_max_pct_below):
                continue
            if not _passes_setup_rules(row, spec):
                continue
        elif not _passes_setup_rules(row, spec):
            continue

        snap = {
            "symbol": row["symbol"], "date": row["date"],
            "close": row.get("close"),
            "prior_move_pct": row.get("prior_move_pct"),
            "prior_move_bars": row.get("prior_move_bars"),
            "base_length": row.get("consolidation_days"),
            "base_depth_pct": row.get("consolidation_depth_pct"),
            "base_range_contraction": row.get("range_contraction"),
            "base_volume_contraction": row.get("volume_contraction"),
            "base_quality": row.get("base_quality"),
            "base_qualifies": bool(row.get("base_qualifies")),
            "breakout_level": row.get("consolidation_high"),
            "rvol": row.get("breakout_volume_ratio") if triggered else row.get("relative_volume"),
            "adr_pct": row.get("adr20"),
            "pct_above_sma20": row.get("distance_from_20sma"),
            "rs_percentile": row.get("relative_strength_pct"),
            "sector_percentile": row.get("sector_rank_percentile"),
            "r2_20": None,
            "dollar_volume_20d": row.get("dollar_volume_20d"),
            "pct_to_breakout": dist,
        }
        comps = _score_components(regime=regime,
                                  sector_pct=row.get("sector_rank_percentile"),
                                  theme_pct=row.get("theme_rank_percentile"),
                                  rs_pct=row.get("relative_strength_pct"),
                                  snap=snap, state=state)
        score = sum(weights[k] * comps[k]["score"] for k in weights)
        for k in comps:
            comps[k]["weight"] = round(weights.get(k, 0.0), 4)
            comps[k]["contribution"] = round(weights.get(k, 0.0) * comps[k]["score"], 2)
        close = row.get("close") or 0.0
        stop = row.get("consolidation_low")
        candidates.append(ScanCandidate(
            symbol=row["symbol"], date=row["date"], state=state, score=round(score, 1),
            components=comps, setup=snap, sector=row.get("sector"),
            themes=row.get("themes") or [], close=round(close, 4),
            breakout_level=(round(row["consolidation_high"], 4)
                            if row.get("consolidation_high") else None),
            pct_to_breakout=round(dist, 2) if dist is not None else None,
            suggested_stop=round(stop, 4) if stop else None,
            risk_pct=(round((close - stop) / close * 100.0, 2)
                      if stop and close else None)))

    candidates.sort(key=lambda c: -c.score)
    top = candidates[:cfg.limit]
    fresh = MARKET.freshness("1d")
    origin = ("SYNTHETIC" if any("synthetic" in (p.get("provider") or "")
                                 for p in fresh["providers"]) else "REAL")
    return {
        "ok": True, "as_of": as_of.isoformat(),
        "strategy": f"{spec.name} v{spec.version}",
        "weights": weights,
        "engine": "precomputed features (SQL)",
        "symbols_scanned": screen["symbols_scanned"],
        "candidates_found": len(candidates),
        "breakouts_today": sum(1 for c in candidates if c.state == "breakout"),
        "approaching": sum(1 for c in candidates if c.state == "approaching"),
        "market_regime": regime.to_dict(),
        "leading_sectors": [{"name": g["name"], "score": g["score"], "rank": g["rank"]}
                            for g in stored_sectors[:5]],
        "leading_themes": [{"name": g["name"], "score": g["score"], "rank": g["rank"]}
                           for g in stored_themes[:5]],
        "results": [c.to_dict() for c in top],
        "data_origin": origin,
        "data_provider": fresh.get("primary_provider") or "db",
        "data_freshness": fresh,
        "seconds": round(_time.time() - t0, 3),
        "claim": Claim(
            statement=(f"{len(candidates)} symbols of {screen['symbols_scanned']} with "
                       f"precomputed features matched the '{spec.name} v{spec.version}' "
                       f"setup definition on {as_of.isoformat()} "
                       f"({sum(1 for c in candidates if c.state == 'breakout')} triggered)."),
            evidence=EvidenceClass.LIVE_MARKET_OBSERVATION,
            value=len(candidates), sample_size=screen["symbols_scanned"],
            period=as_of.isoformat(),
            data_source=fresh.get("primary_provider") or "db",
            data_origin=DataOrigin(origin) if origin in DataOrigin.__members__
            else DataOrigin.UNKNOWN,
            methodology=("Read from precomputed strategy_features (feature engine), then "
                         "scored. No vendor API calls and no indicator recomputation. "
                         "Ranking weights are configurable and unvalidated."),
            caveats=["A high score means the setup matches the definition well. It is not "
                     "a probability, an expected return, or a recommendation.",
                     f"Features were last computed at "
                     f"{MARKET.feature_coverage()['last_computed_at']}; anything more "
                     "recent than that is not reflected."],
        ).to_dict(),
    }


def _stored_regime(store: Any, as_of: dt.date) -> MarketRegime | None:
    """Rehydrate a persisted regime row into the object the scorer expects."""
    row = store.one("SELECT * FROM market_regimes WHERE as_of <= ? "
                    "ORDER BY as_of DESC LIMIT 1", (as_of.isoformat(),))
    if not row:
        return None
    import json as _json
    breadth = {"available": row.get("breadth_above_50sma") is not None,
               "pct_above_50sma": row.get("breadth_above_50sma"),
               "pct_above_200sma": row.get("breadth_above_200sma")}
    return MarketRegime(
        as_of=row["as_of"], label=row["label"],
        volatility_label=row.get("volatility_label") or "UNKNOWN",
        strength=float(row.get("strength") or float("nan")),
        indexes={}, breadth=breadth, filter_pass=bool(row.get("filter_pass")),
        filter_detail=f"as stored on {row['as_of']}",
        data_origin=row.get("origin") or "UNKNOWN",
        data_provider=row.get("provider") or "db",
        components=_json.loads(row.get("components") or "{}"))


def _passes_setup_rules(row: dict[str, Any], spec: StrategySpec) -> bool:
    """The spec's setup gates, evaluated against a stored feature row."""
    s = spec.setup
    if (row.get("prior_move_pct") or -1e9) < s.prior_move_min_pct:
        return False
    for length, key in ((10, "above_10sma"), (20, "above_20sma"),
                        (50, "above_50sma"), (200, "above_200sma")):
        if length in s.require_close_above_smas and not row.get(key):
            return False
    if s.require_sma_stack == [10, 20, 50] and not row.get("sma_stack_10_20_50"):
        return False
    if s.max_pct_above_sma20 is not None:
        d = row.get("distance_from_20sma")
        if d is not None and d > s.max_pct_above_sma20:
            return False
    if s.min_rs_percentile is not None:
        rs = row.get("relative_strength_pct")
        if rs is None or rs < s.min_rs_percentile:
            return False
    if (row.get("base_quality") or 0.0) < s.cons_min_quality:
        return False
    cd = row.get("consolidation_days")
    if cd is not None and not (s.cons_min_len <= cd <= s.cons_max_len):
        return False
    return True


def _scan_recompute(cfg: ScanConfig = ScanConfig(), hub: DataHub = HUB,
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
        "engine": "recomputed from bars",
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
