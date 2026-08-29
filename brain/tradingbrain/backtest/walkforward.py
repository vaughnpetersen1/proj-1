"""Walk-forward and out-of-sample testing.

A single backtest over all available history tells you what a parameter set did
after you already knew which parameter set to pick. Walk-forward is the cheapest
honest antidote: choose parameters on data the evaluation never sees, then
report the *test* result and the degradation between the two.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from ..data.panel import Panel, build_panel
from ..data.providers.registry import HUB, DataHub
from ..provenance import Claim, DataOrigin, EvidenceClass
from ..strategy.spec import StrategySpec
from .engine import Backtester


OBJECTIVES: dict[str, Callable[[dict[str, Any]], float]] = {
    "expectancy_r": lambda m: _f(m.get("expectancy_r")),
    "profit_factor": lambda m: _f(m.get("profit_factor")),
    "sharpe": lambda m: _f(m.get("sharpe")),
    "total_return_pct": lambda m: _f(m.get("total_return_pct")),
    "calmar": lambda m: _f(m.get("calmar")),
}


def _f(v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float("-inf")
    return x if np.isfinite(x) else float("-inf")


def _apply(spec: StrategySpec, params: dict[str, Any]) -> StrategySpec:
    """Apply dotted-path overrides (e.g. 'setup.prior_move_min_pct': 40) safely."""
    d = spec.to_dict()
    for path, value in params.items():
        parts = path.split(".")
        node = d
        for p in parts[:-1]:
            if p not in node or not isinstance(node[p], dict):
                raise KeyError(f"unknown parameter path {path!r}")
            node = node[p]
        if parts[-1] not in node:
            raise KeyError(f"unknown parameter {path!r}")
        node[parts[-1]] = value
    return StrategySpec.from_dict(d)


@dataclasses.dataclass
class Window:
    train_start: dt.date
    train_end: dt.date
    test_start: dt.date
    test_end: dt.date

    def to_dict(self) -> dict[str, str]:
        return {k: v.isoformat() for k, v in dataclasses.asdict(self).items()}


def make_windows(start: dt.date, end: dt.date, train_years: float = 4.0,
                 test_years: float = 1.0, step_years: float | None = None) -> list[Window]:
    step = step_years or test_years
    out: list[Window] = []
    cursor = start
    while True:
        tr_end = cursor + dt.timedelta(days=int(train_years * 365.25))
        te_end = tr_end + dt.timedelta(days=int(test_years * 365.25))
        if tr_end >= end:
            break
        out.append(Window(cursor, tr_end, tr_end + dt.timedelta(days=1), min(te_end, end)))
        if te_end >= end:
            break
        cursor = cursor + dt.timedelta(days=int(step * 365.25))
    return out


def run_walk_forward(spec: StrategySpec, grid: dict[str, Sequence[Any]],
                     start: dt.date, end: dt.date, hub: DataHub = HUB,
                     train_years: float = 4.0, test_years: float = 1.0,
                     objective: str = "expectancy_r", symbols: Iterable[str] | None = None,
                     min_trades: int = 20, panel: Panel | None = None) -> dict[str, Any]:
    if objective not in OBJECTIVES:
        raise ValueError(f"unknown objective {objective!r}; known: {sorted(OBJECTIVES)}")
    score = OBJECTIVES[objective]
    panel = panel if panel is not None else build_panel(symbols, hub, start, end)
    windows = make_windows(start, end, train_years, test_years)
    if not windows:
        return {"ok": False, "reason": "the period is too short for even one train/test split"}

    keys = list(grid)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))]
    folds: list[dict[str, Any]] = []
    origin = provider = "unknown"

    for w in windows:
        train_scores: list[dict[str, Any]] = []
        for params in combos:
            try:
                s = _apply(spec, params)
            except (KeyError, ValueError) as exc:
                train_scores.append({"params": params, "error": str(exc), "score": float("-inf")})
                continue
            res = Backtester(s, hub, w.train_start, w.train_end, panel=panel).run()
            origin, provider = res.data_origin, res.data_provider
            sc = score(res.metrics) if res.metrics["n_trades"] >= min_trades else float("-inf")
            train_scores.append({"params": params, "score": sc,
                                 "n_trades": res.metrics["n_trades"],
                                 "expectancy_r": res.metrics["expectancy_r"],
                                 "profit_factor": res.metrics["profit_factor"]})
        viable = [t for t in train_scores if np.isfinite(t["score"])]
        if not viable:
            folds.append({"window": w.to_dict(), "selected": None,
                          "reason": f"no parameter set produced >= {min_trades} training trades",
                          "train": train_scores})
            continue
        best = max(viable, key=lambda t: t["score"])
        test_spec = _apply(spec, best["params"])
        test = Backtester(test_spec, hub, w.test_start, w.test_end, panel=panel).run()
        folds.append({
            "window": w.to_dict(),
            "selected": best["params"],
            "n_candidates": len(combos),
            "train_score": best["score"],
            "train_metrics": {k: best.get(k) for k in ("n_trades", "expectancy_r", "profit_factor")},
            "test_metrics": {k: test.metrics.get(k) for k in
                             ("n_trades", "win_rate_pct", "expectancy_r", "profit_factor",
                              "total_return_pct", "max_drawdown_pct", "sharpe")},
            "test_score": score(test.metrics),
            "degradation": (score(test.metrics) - best["score"]),
        })

    scored = [f for f in folds if f.get("selected") is not None]
    tr = np.array([f["train_score"] for f in scored], float) if scored else np.array([])
    te = np.array([f["test_score"] for f in scored], float) if scored else np.array([])
    te_finite = te[np.isfinite(te)]
    summary: dict[str, Any] = {
        "ok": bool(scored),
        "objective": objective,
        "n_windows": len(windows),
        "n_scored": len(scored),
        "grid_size": len(combos),
        "mean_train_score": float(tr[np.isfinite(tr)].mean()) if np.isfinite(tr).any() else None,
        "mean_test_score": float(te_finite.mean()) if len(te_finite) else None,
        "median_test_score": float(np.median(te_finite)) if len(te_finite) else None,
        "pct_test_windows_positive": float((te_finite > 0).mean() * 100) if len(te_finite) else None,
        "parameter_stability": _stability(scored),
        "folds": folds,
        "windows": [w.to_dict() for w in windows],
    }
    if summary["mean_train_score"] is not None and summary["mean_test_score"] is not None:
        summary["degradation"] = summary["mean_test_score"] - summary["mean_train_score"]
        drop = summary["degradation"]
        summary["verdict"] = (
            "OUT_OF_SAMPLE_HOLDS" if drop > -0.1 * abs(summary["mean_train_score"] or 1)
            else "SUBSTANTIAL_DEGRADATION" if summary["mean_test_score"] > 0
            else "FAILS_OUT_OF_SAMPLE")
    else:
        summary["verdict"] = "INSUFFICIENT_DATA"

    summary["claim"] = Claim(
        statement=(f"Walk-forward over {len(scored)} train/test folds selecting on "
                   f"'{objective}': mean in-sample {summary['mean_train_score']}, mean "
                   f"out-of-sample {summary['mean_test_score']} "
                   f"({summary['verdict']})."),
        evidence=EvidenceClass.BACKTEST_RESULT,
        sample_size=len(scored),
        period=f"{start.isoformat()}..{end.isoformat()}",
        definition_of_success=f"higher {objective} on the untouched test window",
        data_source=provider,
        data_origin=DataOrigin(origin) if origin in DataOrigin.__members__ else DataOrigin.UNKNOWN,
        methodology=(f"Anchored-start rolling windows: {train_years}y train, {test_years}y "
                     f"test. {len(combos)} parameter combinations searched per fold; the "
                     "best training set is evaluated once on the test window."),
        caveats=["Selecting on the training window and reporting the test window is honest "
                 "only if the test window is never used to choose anything. Re-running this "
                 "with a different grid after seeing test results reintroduces the bias.",
                 f"Searching {len(combos)} combinations per fold inflates the best training "
                 "score by chance alone; the degradation figure is the number to read."],
    ).to_dict()
    return summary


def _stability(folds: list[dict[str, Any]]) -> dict[str, Any]:
    """How often did the search settle on the same parameters?

    A strategy whose optimal parameters jump every fold is a strategy whose
    'optimum' is noise.
    """
    if not folds:
        return {}
    out: dict[str, Any] = {}
    keys = set().union(*[set(f["selected"]) for f in folds])
    for k in sorted(keys):
        vals = [f["selected"].get(k) for f in folds]
        uniq = {}
        for v in vals:
            uniq[str(v)] = uniq.get(str(v), 0) + 1
        mode, cnt = max(uniq.items(), key=lambda kv: kv[1])
        out[k] = {"values": vals, "modal_value": mode,
                  "modal_share_pct": round(100.0 * cnt / len(vals), 1),
                  "distinct_choices": len(uniq)}
    return out
