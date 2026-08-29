"""Parameter sensitivity analysis.

An edge that exists only at prior_move >= 30% and vanishes at 28% and 32% is a
curve-fit, not an edge. This sweeps one parameter at a time and reports how much
the result moves -- plus a blunt verdict when the surface is a spike.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Sequence

import numpy as np

from ..data.panel import Panel, build_panel
from ..data.providers.registry import HUB, DataHub
from ..provenance import Claim, DataOrigin, EvidenceClass
from ..strategy.spec import StrategySpec
from .engine import Backtester
from .walkforward import OBJECTIVES, _apply


def run_sensitivity(spec: StrategySpec, sweeps: dict[str, Sequence[Any]],
                    start: dt.date | None = None, end: dt.date | None = None,
                    hub: DataHub = HUB, objective: str = "expectancy_r",
                    symbols: Iterable[str] | None = None,
                    panel: Panel | None = None) -> dict[str, Any]:
    score = OBJECTIVES[objective]
    panel = panel if panel is not None else build_panel(symbols, hub, start, end)
    baseline = Backtester(spec, hub, start, end, panel=panel).run()
    base_score = score(baseline.metrics)

    results: dict[str, Any] = {}
    for path, values in sweeps.items():
        points = []
        for v in values:
            try:
                s = _apply(spec, {path: v})
            except (KeyError, ValueError) as exc:
                points.append({"value": v, "error": str(exc)})
                continue
            r = Backtester(s, hub, start, end, panel=panel).run()
            points.append({
                "value": v, "score": score(r.metrics),
                "n_trades": r.metrics["n_trades"],
                "expectancy_r": r.metrics["expectancy_r"],
                "win_rate_pct": r.metrics["win_rate_pct"],
                "profit_factor": r.metrics["profit_factor"],
                "max_drawdown_pct": r.metrics["max_drawdown_pct"],
                "total_return_pct": r.metrics["total_return_pct"],
            })
        scores = np.array([p["score"] for p in points
                           if "score" in p and np.isfinite(p["score"])], float)
        verdict, detail = _verdict(scores, base_score)
        results[path] = {"points": points, "verdict": verdict, "detail": detail,
                         "mean": float(scores.mean()) if len(scores) else None,
                         "stdev": float(scores.std(ddof=1)) if len(scores) > 1 else None,
                         "range": [float(scores.min()), float(scores.max())] if len(scores) else None}

    fragile = [k for k, v in results.items() if v["verdict"] == "FRAGILE"]
    return {
        "objective": objective,
        "baseline_score": base_score,
        "baseline_metrics": {k: baseline.metrics.get(k) for k in
                             ("n_trades", "win_rate_pct", "expectancy_r", "profit_factor",
                              "total_return_pct", "max_drawdown_pct")},
        "sweeps": results,
        "fragile_parameters": fragile,
        "overall_verdict": ("FRAGILE" if fragile else
                            "ROBUST_ACROSS_SWEPT_PARAMETERS" if results else "NOT_TESTED"),
        "claim": Claim(
            statement=(f"Swept {len(results)} parameter(s) around the baseline "
                       f"{objective}={base_score:.4g}. "
                       + (f"Result is fragile in: {', '.join(fragile)}." if fragile
                          else "No swept parameter produced a spike-shaped surface.")),
            evidence=EvidenceClass.BACKTEST_RESULT,
            sample_size=baseline.metrics["n_trades"],
            period=f"{baseline.start}..{baseline.end}",
            data_source=baseline.data_provider,
            data_origin=DataOrigin(baseline.data_origin)
            if baseline.data_origin in DataOrigin.__members__ else DataOrigin.UNKNOWN,
            methodology="One-at-a-time sweep holding all other parameters at the baseline. "
                        "Does not explore interactions between parameters.",
            caveats=["One-at-a-time sweeps miss interaction effects; a pair of parameters can "
                     "each look robust alone and be jointly fitted."],
        ).to_dict(),
    }


def _verdict(scores: np.ndarray, base: float) -> tuple[str, str]:
    if len(scores) < 3:
        return "INSUFFICIENT_POINTS", "fewer than 3 evaluable points on the sweep"
    best = float(scores.max())
    median = float(np.median(scores))
    positive = float((scores > 0).mean())
    if best <= 0:
        return "NO_EDGE_ANYWHERE", "no parameter value produced a positive objective"
    # A spike: the best point towers over the typical point, and most of the
    # neighbourhood is unprofitable.
    if median <= 0 or (best - median) > 0.6 * abs(best) or positive < 0.5:
        return "FRAGILE", (f"best={best:.4g} vs median={median:.4g}; only "
                           f"{positive:.0%} of swept values kept the objective positive. "
                           "This looks like a spike, not a plateau.")
    return "ROBUST", (f"best={best:.4g}, median={median:.4g}, "
                      f"{positive:.0%} of swept values stayed positive -- a plateau.")
