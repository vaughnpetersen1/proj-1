"""The Hypothesis Lab.

Three experiment kinds, all reproducible from what is stored:

``split``    Take one setup definition, find every historical occurrence, split
             the occurrences by a measured feature, and compare the forward
             outcomes of the two groups. Answers "does X improve Y?".

``variants`` Take a qualitative concept, backtest each competing quantitative
             definition over the same period and universe, and report whether
             the concept survives the choice of definition.

``filter``   Run the same strategy with a rule on and off. Answers "is this
             filter earning its place?".

Every experiment records its specification, its data origin, its statistics and
a verdict drawn from {SUPPORTED, MIXED, UNSUPPORTED, INSUFFICIENT_DATA}.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import time
from typing import Any, Callable, Sequence

import numpy as np

from ..data.panel import Panel, build_panel
from ..data.providers.registry import HUB, DataHub
from ..db.store import STORE, Store
from ..provenance import Claim, DataOrigin, EvidenceClass
from ..stats.core import (bootstrap_ci, conclusion_from, describe, permutation_test,
                          sample_size_verdict, welch_t, wilson_interval)
from ..stats.analogs import AnalogQuery, find_historical_setups
from ..strategy.library import get_strategy
from ..strategy.spec import StrategySpec
from .formalizer import CONCEPTS

CODE_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# feature predicates available to `split` experiments
# ---------------------------------------------------------------------------

def _feat(name: str) -> Callable[[dict[str, Any]], float | None]:
    def get(snap: dict[str, Any]) -> float | None:
        v = snap.get(name)
        try:
            f = float(v)
            return f if np.isfinite(f) else None
        except (TypeError, ValueError):
            return None
    return get


SPLIT_FEATURES: dict[str, dict[str, Any]] = {
    "volume_contraction": {
        "getter": _feat("base_volume_contraction"),
        "threshold": 1.0, "below_label": "volume contracted during the base",
        "above_label": "volume did not contract",
        "question": "Does volume contraction during consolidation improve breakout expectancy?",
        "hypothesised_better": "below",
    },
    "range_contraction": {
        "getter": _feat("base_range_contraction"),
        "threshold": 1.0, "below_label": "range tightened during the base",
        "above_label": "range did not tighten",
        "question": "Does a tightening range during consolidation improve breakout outcomes?",
        "hypothesised_better": "below",
    },
    "prior_move": {
        "getter": _feat("prior_move_pct"),
        "threshold": 50.0, "below_label": "prior move below 50%",
        "above_label": "prior move 50% or more",
        "question": "Do larger prior advances produce better breakouts?",
        "hypothesised_better": "above",
    },
    "base_quality": {
        "getter": _feat("base_quality"),
        "threshold": 0.7, "below_label": "base quality below 0.70",
        "above_label": "base quality 0.70 or above",
        "question": "Does the composite base-quality score separate good breakouts from bad?",
        "hypothesised_better": "above",
    },
    "rvol": {
        "getter": _feat("rvol"), "threshold": 2.0,
        "below_label": "breakout relative volume below 2x",
        "above_label": "breakout relative volume 2x or more",
        "question": "Does higher breakout volume improve outcomes?",
        "hypothesised_better": "above",
    },
    "relative_strength": {
        "getter": _feat("rs_percentile"), "threshold": 80.0,
        "below_label": "relative strength below the 80th percentile",
        "above_label": "relative strength in the top 20%",
        "question": "Does relative strength improve breakout expectancy?",
        "hypothesised_better": "above",
    },
    "extension": {
        "getter": _feat("pct_above_sma20"), "threshold": 10.0,
        "below_label": "within 10% of the 20 SMA",
        "above_label": "more than 10% above the 20 SMA (extended)",
        "question": "Are extended breakouts worse than non-extended ones?",
        "hypothesised_better": "below",
    },
    "base_length": {
        "getter": _feat("base_length"), "threshold": 15.0,
        "below_label": "base shorter than 15 bars", "above_label": "base 15 bars or longer",
        "question": "Does base length matter?",
        "hypothesised_better": None,
    },
    "sector_strength": {
        "getter": _feat("sector_percentile"), "threshold": 50.0,
        "below_label": "sector in the bottom half", "above_label": "sector in the top half",
        "question": "Does sector strength improve breakout expectancy?",
        "hypothesised_better": "above",
    },
}


@dataclasses.dataclass
class HypothesisSpec:
    kind: str = "split"                      # split | variants | filter
    question: str = ""
    statement: str = ""
    strategy_key: str = "sar_v1_0"
    feature: str | None = None               # for `split`
    threshold: float | None = None
    concept: str | None = None               # for `variants`
    overrides: list[dict[str, Any]] | None = None
    filter_path: str | None = None           # for `filter`
    horizon: int = 10
    start: str | None = None
    end: str | None = None
    symbols: list[str] | None = None
    target_r: float = 3.0
    apply_market_filter: bool = False
    #: `variants` and `filter` run real backtests; keep the universe modest or
    #: the experiment takes minutes.
    max_symbols: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def create_hypothesis(question: str, statement: str = "", category: str = "",
                      spec: HypothesisSpec | None = None,
                      store: Store = STORE) -> dict[str, Any]:
    spec = spec or infer_spec(question)
    hid = store.add_hypothesis(question=question,
                               statement=statement or spec.statement or question,
                               category=category or _category_for(spec),
                               origin="operator", spec=spec.to_dict())
    return {"id": hid, "question": question, "spec": spec.to_dict(),
            "status": "OPEN",
            "note": "Created but not tested. It carries no evidential weight until run."}


def _category_for(spec: HypothesisSpec) -> str:
    if spec.kind == "filter":
        return "MARKET_REGIME"
    if spec.concept and spec.concept.lower() in CONCEPTS:
        return CONCEPTS[spec.concept.lower()]["category"]
    return "SETUP"


def infer_spec(question: str) -> HypothesisSpec:
    """Map a plain-English question onto an experiment.

    Deliberately conservative: if nothing matches, it says so instead of
    guessing an experiment that does not answer the question.
    """
    q = question.lower()
    for concept in CONCEPTS:
        if concept in q and ("which" in q or "definition" in q or "compare" in q
                             or "best" in q):
            return HypothesisSpec(kind="variants", question=question, concept=concept,
                                  statement=f"Competing definitions of '{concept}' produce "
                                            "materially different results.")
    if ("market" in q or "spy" in q or "qqq" in q or "regime" in q) and \
            ("filter" in q or "avoid" in q or "improve" in q):
        return HypothesisSpec(kind="filter", question=question,
                              filter_path="market_filter.enabled",
                              statement="The index moving-average filter improves results.")
    if ("10" in q and "20" in q) and ("sma" in q or "moving average" in q or "trail" in q):
        return HypothesisSpec(kind="variants", question=question, concept="trailing stop",
                              statement="Trailing with the 10 SMA differs from the 20 SMA.")
    for key, meta in SPLIT_FEATURES.items():
        tokens = key.split("_")
        if all(t in q for t in tokens) or meta["question"].lower()[:40] in q:
            return HypothesisSpec(kind="split", question=question, feature=key,
                                  statement=meta["question"])
    # sensible default: the operator's own worked example from the brief
    return HypothesisSpec(kind="split", question=question, feature="volume_contraction",
                          statement=("No specific experiment matched this question; defaulted "
                                     "to the volume-contraction split. Re-run with an explicit "
                                     "feature to test something else."))


# ---------------------------------------------------------------------------

def run_hypothesis_test(spec: HypothesisSpec, hypothesis_id: int | None = None,
                        hub: DataHub = HUB, store: Store = STORE,
                        panel: Panel | None = None) -> dict[str, Any]:
    t0 = time.time()
    if spec.kind == "split":
        res = _run_split(spec, hub, panel)
    elif spec.kind == "variants":
        res = _run_variants(spec, hub, panel)
    elif spec.kind == "filter":
        res = _run_filter(spec, hub, panel)
    else:
        raise ValueError(f"unknown experiment kind {spec.kind!r}")
    res["runtime_seconds"] = round(time.time() - t0, 2)
    res["code_version"] = CODE_VERSION
    res["spec"] = spec.to_dict()

    eid = store.add_experiment(
        hypothesis_id=hypothesis_id, name=res.get("name", spec.question or spec.kind),
        spec=spec.to_dict(), results=res, conclusion=res.get("conclusion"),
        verdict=res.get("verdict"), sample_size=res.get("sample_size"),
        data_origin=res.get("data_origin"), data_provider=res.get("data_provider"),
        code_version=CODE_VERSION, runtime_seconds=res["runtime_seconds"])
    res["experiment_id"] = eid
    return res


def _dates(spec: HypothesisSpec) -> tuple[dt.date | None, dt.date | None]:
    s = dt.date.fromisoformat(spec.start) if spec.start else None
    e = dt.date.fromisoformat(spec.end) if spec.end else None
    return s, e


def _run_split(spec: HypothesisSpec, hub: DataHub, panel: Panel | None) -> dict[str, Any]:
    feature = spec.feature or "volume_contraction"
    meta_f = SPLIT_FEATURES.get(feature)
    if meta_f is None:
        return {"verdict": "INSUFFICIENT_DATA",
                "conclusion": f"unknown split feature {feature!r}",
                "available_features": sorted(SPLIT_FEATURES)}
    threshold = spec.threshold if spec.threshold is not None else meta_f["threshold"]
    start, end = _dates(spec)

    # A permissive base definition so both arms have events: the split, not the
    # strategy's own filters, must decide group membership.
    base = get_strategy(spec.strategy_key)
    d = base.to_dict()
    d["setup"]["cons_max_volume_ratio"] = 99.0
    d["setup"]["cons_max_range_ratio"] = 99.0
    if feature in ("relative_strength",):
        d["setup"]["min_rs_percentile"] = 0.0
    permissive = StrategySpec.from_dict(d)

    q = AnalogQuery(spec=permissive, start=start, end=end, symbols=spec.symbols,
                    horizons=(1, 3, 5, spec.horizon, 20), target_r=spec.target_r,
                    apply_market_filter=spec.apply_market_filter, max_events=20_000)
    events, meta = find_historical_setups(q, hub, panel)

    get = meta_f["getter"]
    a_vals, b_vals, a_paths, b_paths = [], [], [], []
    for e in events:
        v = get(e.snapshot)
        if v is None:
            continue
        r = e.forward_returns.get(spec.horizon)
        if r is None:
            continue
        (a_vals if v < threshold else b_vals).append(r)
        if e.hit_target_first is not None:
            (a_paths if v < threshold else b_paths).append(e.hit_target_first)

    group_a = {"label": meta_f["below_label"], "n": len(a_vals),
               "stats": describe(a_vals, "forward return %")}
    group_b = {"label": meta_f["above_label"], "n": len(b_vals),
               "stats": describe(b_vals, "forward return %")}
    for g, paths in ((group_a, a_paths), (group_b, b_paths)):
        if paths:
            hits = sum(paths)
            ci = wilson_interval(hits, len(paths))
            g["pct_hit_target_first"] = round(100.0 * hits / len(paths), 2)
            g["pct_hit_target_first_ci"] = [round(ci.ci_low * 100, 2),
                                            round(ci.ci_high * 100, 2)]
            g["resolved"] = len(paths)
    for g, vals in ((group_a, a_vals), (group_b, b_vals)):
        if len(vals) >= 3:
            pos = sum(1 for v in vals if v > 0)
            ci = wilson_interval(pos, len(vals))
            g["pct_positive"] = round(100.0 * pos / len(vals), 2)
            g["pct_positive_ci"] = [round(ci.ci_low * 100, 2), round(ci.ci_high * 100, 2)]
            b = bootstrap_ci(vals, np.mean, n_boot=2000, name="mean return")
            g["mean_ci"] = [round(b.ci_low, 3), round(b.ci_high, 3)] if b.ci_low is not None else None

    perm = permutation_test(a_vals, b_vals, np.mean, n_perm=5000,
                            name=f"{feature}: below vs above {threshold}")
    welch = welch_t(a_vals, b_vals)
    n_min = min(len(a_vals), len(b_vals))
    stat_verdict = conclusion_from(perm.p_value, n_min, perm.effect_size)

    # Direction matters. A significant effect pointing the OPPOSITE way to the
    # hypothesis is not "supported" -- it is evidence against the source's claim,
    # which is a more useful finding than a null result and must not be reported
    # as agreement.
    expected = meta_f.get("hypothesised_better")
    winner = ("below" if (perm.estimate or 0) > 0 else "above") if perm.estimate else None
    winner_label = group_a["label"] if winner == "below" else group_b["label"]
    direction_note = ""
    verdict = stat_verdict
    if expected and winner and stat_verdict in ("SUPPORTED", "MIXED"):
        if winner != expected:
            verdict = "UNSUPPORTED"
            direction_note = (
                " DIRECTION REVERSED: the difference is statistically detectable but points "
                f"AGAINST the hypothesis -- '{winner_label}' performed better, while the "
                "source material implies the opposite. Treat this as evidence against the "
                "source's claim on this data, not as confirmation of it.")
        elif stat_verdict == "SUPPORTED":
            direction_note = " The effect points in the direction the source material implies."

    conclusion = (
        f"{group_a['label']}: mean {group_a['stats'].get('mean', float('nan')):.3f}% over "
        f"{group_a['n']} events. {group_b['label']}: mean "
        f"{group_b['stats'].get('mean', float('nan')):.3f}% over {group_b['n']} events. "
        f"Difference {perm.estimate:.3f} percentage points in favour of "
        f"'{winner_label}', permutation p={perm.p_value:.4f}, Cohen's "
        f"d={perm.effect_size if perm.effect_size is None else round(perm.effect_size, 3)}. "
        f"Verdict: {verdict}.{direction_note}"
        if perm.p_value is not None else
        f"Insufficient data: group sizes {group_a['n']} and {group_b['n']}.")

    return {
        "name": f"split:{feature}",
        "kind": "split",
        "question": spec.question or meta_f["question"],
        "feature": feature, "threshold": threshold, "horizon": spec.horizon,
        "groups": [group_a, group_b],
        "tests": {"permutation": perm.to_dict(), "welch_t": welch.to_dict()},
        "sample_size": len(a_vals) + len(b_vals),
        "sample_verdict": sample_size_verdict(n_min),
        "hypothesised_better_group": expected,
        "observed_better_group": winner,
        "direction_matches_hypothesis": (None if not (expected and winner)
                                         else winner == expected),
        "statistical_verdict_ignoring_direction": stat_verdict,
        "verdict": verdict,
        "conclusion": conclusion,
        "data_origin": meta.get("data_origin"), "data_provider": meta.get("data_provider"),
        "meta": meta,
        "claim": Claim(
            statement=conclusion, evidence=EvidenceClass.BACKTEST_RESULT,
            value=perm.estimate, sample_size=len(a_vals) + len(b_vals),
            period=meta.get("period"),
            definition_of_success=f"mean {spec.horizon}-bar forward return from the next open",
            data_source=meta.get("data_provider", "unknown"),
            data_origin=DataOrigin(meta.get("data_origin", "UNKNOWN"))
            if meta.get("data_origin") in DataOrigin.__members__ else DataOrigin.UNKNOWN,
            methodology="Two-sided permutation test on the difference in means, 5000 shuffles; "
                        "Welch's t reported alongside for comparison.",
            caveats=["Events overlap in time and correlate across names, so the effective "
                     "sample is smaller than N and p-values are optimistic.",
                     "This is one split at one threshold. Trying many thresholds and "
                     "reporting the best is data dredging; the threshold here is fixed in "
                     "advance in SPLIT_FEATURES."],
        ).to_dict(),
    }


def _run_variants(spec: HypothesisSpec, hub: DataHub, panel: Panel | None) -> dict[str, Any]:
    from ..backtest.engine import Backtester
    from ..backtest.walkforward import _apply

    concept = (spec.concept or "").lower()
    overrides = spec.overrides
    if overrides is None:
        c = CONCEPTS.get(concept)
        if not c:
            return {"verdict": "INSUFFICIENT_DATA",
                    "conclusion": f"unknown concept {spec.concept!r}",
                    "known_concepts": sorted(CONCEPTS)}
        overrides = c["candidates"]

    start, end = _dates(spec)
    base = get_strategy(spec.strategy_key)
    panel = panel if panel is not None else build_panel(
        spec.symbols, hub, start, end, max_symbols=spec.max_symbols)

    rows = []
    for cand in overrides:
        label = cand.get("label", str(cand.get("params")))
        try:
            s = _apply(base, cand["params"])
        except (KeyError, ValueError) as exc:
            rows.append({"label": label, "error": str(exc)})
            continue
        r = Backtester(s, hub, start, end, panel=panel).run()
        rs = [t.r_multiple for t in r.trades]
        ci = bootstrap_ci(rs, np.mean, n_boot=1500, name="expectancy (R)") if len(rs) >= 3 else None
        rows.append({
            "label": label, "params": cand["params"],
            "n_trades": r.metrics["n_trades"],
            "win_rate_pct": round(r.metrics["win_rate_pct"], 2)
            if r.metrics["n_trades"] else None,
            "expectancy_r": round(r.metrics["expectancy_r"], 4)
            if r.metrics["n_trades"] else None,
            "expectancy_ci": [round(ci.ci_low, 4), round(ci.ci_high, 4)]
            if ci and ci.ci_low is not None else None,
            "profit_factor": r.metrics["profit_factor"],
            "total_return_pct": round(r.metrics["total_return_pct"], 2),
            "max_drawdown_pct": round(r.metrics["max_drawdown_pct"], 2),
            "sharpe": r.metrics["sharpe"],
            "sample_verdict": sample_size_verdict(r.metrics["n_trades"]),
        })
        data_origin, data_provider = r.data_origin, r.data_provider

    scored = [r for r in rows if r.get("expectancy_r") is not None and r["n_trades"] >= 20]
    scored.sort(key=lambda r: -r["expectancy_r"])
    if len(scored) < 2:
        verdict = "INSUFFICIENT_DATA"
        conclusion = (f"Only {len(scored)} of {len(rows)} candidate definitions produced at "
                      "least 20 trades. The concept cannot be compared on this data.")
    else:
        best, worst = scored[0], scored[-1]
        spread = best["expectancy_r"] - worst["expectancy_r"]
        overlap = _ci_overlap(best.get("expectancy_ci"), worst.get("expectancy_ci"))
        if overlap:
            verdict = "MIXED"
            conclusion = (f"Best definition '{best['label']}' ({best['expectancy_r']}R over "
                          f"{best['n_trades']} trades) and worst '{worst['label']}' "
                          f"({worst['expectancy_r']}R over {worst['n_trades']}) have "
                          "overlapping bootstrap intervals: the ranking is not distinguishable "
                          "from noise on this sample.")
        else:
            verdict = "SUPPORTED"
            conclusion = (f"Definitions differ materially. '{best['label']}' produced "
                          f"{best['expectancy_r']}R ({best['n_trades']} trades) versus "
                          f"'{worst['label']}' at {worst['expectancy_r']}R "
                          f"({worst['n_trades']}), a spread of {spread:.3f}R with "
                          "non-overlapping bootstrap intervals. The choice of "
                          "operationalisation matters and must not be made silently.")
    return {
        "name": f"variants:{concept or 'custom'}",
        "kind": "variants", "question": spec.question,
        "concept": spec.concept,
        "ambiguity_note": CONCEPTS.get(concept, {}).get("ambiguity"),
        "rows": rows, "ranked": scored,
        "sample_size": sum(r.get("n_trades", 0) for r in rows),
        "verdict": verdict, "conclusion": conclusion,
        "data_origin": locals().get("data_origin", "UNKNOWN"),
        "data_provider": locals().get("data_provider", "unknown"),
        "caveats": ["Comparing many definitions on one period and picking the best is exactly "
                    "how overfitting happens. Confirm the winner with walk-forward before "
                    "changing any strategy version.",
                    "All candidates share one universe and period, so differences are "
                    "attributable to the definition -- but only within that period."],
    }


def _ci_overlap(a: list[float] | None, b: list[float] | None) -> bool:
    if not a or not b:
        return True
    return not (a[0] > b[1] or b[0] > a[1])


def _run_filter(spec: HypothesisSpec, hub: DataHub, panel: Panel | None) -> dict[str, Any]:
    path = spec.filter_path or "market_filter.enabled"
    sp = HypothesisSpec(kind="variants", question=spec.question or f"Does {path} help?",
                        strategy_key=spec.strategy_key, start=spec.start, end=spec.end,
                        symbols=spec.symbols, max_symbols=spec.max_symbols,
                        overrides=[{"label": f"{path} = ON", "params": {path: True}},
                                   {"label": f"{path} = OFF", "params": {path: False}}])
    res = _run_variants(sp, hub, panel)
    res["kind"] = "filter"
    res["name"] = f"filter:{path}"
    on = next((r for r in res["rows"] if r["label"].endswith("ON")), None)
    off = next((r for r in res["rows"] if r["label"].endswith("OFF")), None)
    if on and off and on.get("expectancy_r") is not None and off.get("expectancy_r") is not None:
        res["comparison"] = {
            "filter": path,
            "with_filter": on, "without_filter": off,
            "expectancy_delta_r": round(on["expectancy_r"] - off["expectancy_r"], 4),
            "drawdown_delta_pct": round(on["max_drawdown_pct"] - off["max_drawdown_pct"], 2),
            "trades_removed": off["n_trades"] - on["n_trades"],
            "win_rate_delta_pct": round((on["win_rate_pct"] or 0) - (off["win_rate_pct"] or 0), 2),
        }
        better = on["expectancy_r"] > off["expectancy_r"]
        res["conclusion"] = (
            f"With the filter: {on['expectancy_r']}R over {on['n_trades']} trades, max "
            f"drawdown {on['max_drawdown_pct']}%. Without: {off['expectancy_r']}R over "
            f"{off['n_trades']} trades, max drawdown {off['max_drawdown_pct']}%. "
            f"The filter {'improved' if better else 'did not improve'} expectancy and "
            f"removed {off['n_trades'] - on['n_trades']} trades. " + res["conclusion"])
    return res


# ---------------------------------------------------------------------------

def list_hypotheses(store: Store = STORE) -> list[dict[str, Any]]:
    return store.hypotheses()


SEED_HYPOTHESES: list[tuple[str, HypothesisSpec]] = [
    ("Does volume contraction during consolidation improve breakout expectancy?",
     HypothesisSpec(kind="split", feature="volume_contraction",
                    statement="Lower volume during consolidation is associated with stronger "
                              "subsequent breakout performance.")),
    ("Does the SPY/QQQ 10>20 SMA filter improve results?",
     HypothesisSpec(kind="filter", filter_path="market_filter.enabled",
                    statement="Skipping breakouts while the index 10 SMA is below the 20 SMA "
                              "improves expectancy and drawdown.")),
    ("Does trailing with the 10 SMA beat the 20 SMA?",
     HypothesisSpec(kind="variants", concept="trailing stop",
                    statement="The two sources reference both; they cannot both be optimal.")),
    ("Does a tightening range during consolidation improve breakout outcomes?",
     HypothesisSpec(kind="split", feature="range_contraction",
                    statement="Bases that tighten outperform bases that do not.")),
    ("Are extended breakouts worse than non-extended ones?",
     HypothesisSpec(kind="split", feature="extension",
                    statement="Buying more than 10% above the 20 SMA reduces expectancy.")),
    ("Does relative strength improve breakout expectancy?",
     HypothesisSpec(kind="split", feature="relative_strength",
                    statement="Top-quintile relative strength improves forward returns.")),
    ("Which definition of 'strong prior move' works best?",
     HypothesisSpec(kind="variants", concept="strong prior move",
                    statement="The concept's value depends on how it is measured.")),
    ("Does sector strength improve breakout expectancy?",
     HypothesisSpec(kind="split", feature="sector_strength",
                    statement="Breakouts in top-half sectors outperform.")),
]


def seed_hypotheses(store: Store = STORE) -> dict[str, Any]:
    added = 0
    for question, spec in SEED_HYPOTHESES:
        if store.one("SELECT id FROM hypotheses WHERE question=?", (question,)):
            continue
        spec.question = question
        store.add_hypothesis(question=question, statement=spec.statement,
                             category=_category_for(spec), origin="seed",
                             spec=spec.to_dict())
        added += 1
    return {"added": added, "total": len(store.hypotheses())}


def research_dashboard(store: Store = STORE) -> dict[str, Any]:
    hyps = store.hypotheses()
    exps = store.experiments(limit=500)
    by_verdict: dict[str, int] = {}
    for e in exps:
        by_verdict[e.get("verdict") or "UNTESTED"] = by_verdict.get(e.get("verdict") or "UNTESTED", 0) + 1

    def bucket(v: str) -> list[dict[str, Any]]:
        return [{"id": e["id"], "name": e["name"], "conclusion": e["conclusion"],
                 "sample_size": e["sample_size"], "data_origin": e["data_origin"],
                 "created_at": e["created_at"]}
                for e in exps if e.get("verdict") == v]

    best = worst = None
    for e in exps:
        rows = ((e.get("results") or {}).get("ranked") or [])
        if rows:
            if best is None or rows[0]["expectancy_r"] > best["expectancy_r"]:
                best = {**rows[0], "experiment_id": e["id"], "experiment": e["name"]}
            if worst is None or rows[-1]["expectancy_r"] < worst["expectancy_r"]:
                worst = {**rows[-1], "experiment_id": e["id"], "experiment": e["name"]}

    return {
        "active_hypotheses": [h for h in hyps if h["status"] == "OPEN"],
        "tested_hypotheses": [h for h in hyps if h["status"] == "TESTED"],
        "counts": {"hypotheses": len(hyps), "experiments": len(exps),
                   "open": sum(1 for h in hyps if h["status"] == "OPEN"),
                   "by_verdict": by_verdict},
        "supported": bucket("SUPPORTED"),
        "mixed": bucket("MIXED"),
        "unsupported": bucket("UNSUPPORTED"),
        "insufficient": bucket("INSUFFICIENT_DATA"),
        "best_variation": best,
        "worst_variation": worst,
        "reproducibility": ("Every experiment stores its full specification, code version, "
                            "data origin and provider. Re-running the stored spec on the same "
                            "data reproduces the result exactly."),
    }
