"""Monte Carlo analysis of a completed backtest.

WHAT THIS IS: a description of the distribution of outcomes you would get if the
trades you actually simulated were reordered or resampled, under the stated
assumptions.

WHAT THIS IS NOT: a forecast. It cannot tell you about setups the backtest never
saw, regimes the period did not contain, or an edge that decays. Every result
carries that caveat because the temptation to read these percentiles as
probabilities of future outcomes is the single most common misuse of the method.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Sequence

import numpy as np

from ..provenance import Claim, DataOrigin, EvidenceClass


@dataclasses.dataclass
class MonteCarloConfig:
    n_simulations: int = 5_000
    method: str = "bootstrap"       # "bootstrap" (resample with replacement) | "shuffle"
    risk_pct: float = 1.0           # fixed-fractional risk per trade
    starting_equity: float = 100_000.0
    n_trades: int | None = None     # defaults to the length of the input sample
    ruin_threshold_pct: float = 50.0
    target_return_pct: float = 100.0
    seed: int = 20260829


def run_monte_carlo(r_multiples: Sequence[float], cfg: MonteCarloConfig = MonteCarloConfig(),
                    data_origin: str = "UNKNOWN", data_provider: str = "unknown") -> dict[str, Any]:
    rs = np.asarray([r for r in r_multiples if np.isfinite(r)], dtype=float)
    n = len(rs)
    if n < 10:
        return {"ok": False,
                "reason": f"Only {n} trades supplied. Monte Carlo on fewer than 10 trades "
                          "describes the noise, not the strategy.",
                "n_trades_input": n}

    k = cfg.n_trades or n
    rng = np.random.default_rng(cfg.seed)
    if cfg.method == "shuffle":
        # reorder the actual trades -- tests path dependence only
        draws = np.array([rng.permutation(rs)[:k] for _ in range(cfg.n_simulations)])
    else:
        draws = rs[rng.integers(0, n, size=(cfg.n_simulations, k))]

    risk = cfg.risk_pct / 100.0
    # Fixed-fractional compounding: equity_{t+1} = equity_t * (1 + risk * R_t)
    growth = 1.0 + risk * draws
    growth = np.maximum(growth, 1e-9)      # a single trade cannot take equity below zero
    paths = cfg.starting_equity * np.cumprod(growth, axis=1)
    paths = np.concatenate([np.full((cfg.n_simulations, 1), cfg.starting_equity), paths], axis=1)

    finals = paths[:, -1]
    peak = np.maximum.accumulate(paths, axis=1)
    dd = (paths / peak - 1.0) * 100.0
    max_dd = dd.min(axis=1)

    losing = draws < 0
    streaks = np.zeros(cfg.n_simulations)
    cur = np.zeros(cfg.n_simulations)
    for t in range(k):
        cur = np.where(losing[:, t], cur + 1, 0)
        streaks = np.maximum(streaks, cur)

    def pct(a: np.ndarray, p: float) -> float:
        return float(np.percentile(a, p))

    ret_pct = (finals / cfg.starting_equity - 1.0) * 100.0
    median_curve = np.percentile(paths, 50, axis=0)
    result = {
        "ok": True,
        "config": dataclasses.asdict(cfg),
        "n_trades_input": n,
        "trades_per_simulation": k,
        "final_equity": {"p05": pct(finals, 5), "p25": pct(finals, 25), "p50": pct(finals, 50),
                         "p75": pct(finals, 75), "p95": pct(finals, 95),
                         "mean": float(finals.mean()), "min": float(finals.min()),
                         "max": float(finals.max())},
        "total_return_pct": {"p05": pct(ret_pct, 5), "p25": pct(ret_pct, 25),
                             "p50": pct(ret_pct, 50), "p75": pct(ret_pct, 75),
                             "p95": pct(ret_pct, 95)},
        "max_drawdown_pct": {"p05": pct(max_dd, 5), "p25": pct(max_dd, 25),
                             "p50": pct(max_dd, 50), "p75": pct(max_dd, 75),
                             "p95": pct(max_dd, 95), "worst": float(max_dd.min())},
        "losing_streak": {"p50": pct(streaks, 50), "p90": pct(streaks, 90),
                          "p99": pct(streaks, 99), "worst": float(streaks.max())},
        "prob_decline_pct": float((finals < cfg.starting_equity).mean() * 100),
        "prob_drawdown_worse_than_20pct": float((max_dd <= -20).mean() * 100),
        "prob_ruin_pct": float((max_dd <= -cfg.ruin_threshold_pct).mean() * 100),
        "prob_reaching_target_pct": float(
            (ret_pct >= cfg.target_return_pct).mean() * 100),
        "equity_percentile_curves": {
            "x": list(range(paths.shape[1])),
            "p05": [round(v, 2) for v in np.percentile(paths, 5, axis=0)],
            "p25": [round(v, 2) for v in np.percentile(paths, 25, axis=0)],
            "p50": [round(v, 2) for v in median_curve],
            "p75": [round(v, 2) for v in np.percentile(paths, 75, axis=0)],
            "p95": [round(v, 2) for v in np.percentile(paths, 95, axis=0)],
        },
        "assumptions": [
            f"Method: {cfg.method}. "
            + ("Trades are resampled WITH replacement from the backtest's R-multiples, so "
               "each simulation assumes the same distribution repeats independently."
               if cfg.method == "bootstrap" else
               "The actual trades are reordered, so the multiset of outcomes is fixed and "
               "only their sequence varies. This isolates path dependence."),
            f"Fixed-fractional sizing at {cfg.risk_pct}% of equity per trade.",
            "Trades are assumed independent and identically distributed. Real trades cluster "
            "by regime and correlate across names, so the true dispersion is WIDER than shown.",
            "The R-multiple distribution is taken from one historical period. If the edge "
            "decays or the regime changes, none of these percentiles apply.",
        ],
    }
    result["claim"] = Claim(
        statement=(f"Across {cfg.n_simulations:,} simulations of {k} trades resampled from "
                   f"{n} backtested trades, the median outcome was "
                   f"{result['total_return_pct']['p50']:.1f}% and the median worst drawdown "
                   f"{result['max_drawdown_pct']['p50']:.1f}%; "
                   f"{result['prob_decline_pct']:.1f}% of simulations ended below the "
                   "starting equity."),
        evidence=EvidenceClass.MODEL_ESTIMATE,
        value=result["total_return_pct"],
        sample_size=n,
        data_source=data_provider,
        data_origin=DataOrigin(data_origin) if data_origin in DataOrigin.__members__
        else DataOrigin.UNKNOWN,
        methodology=f"{cfg.method} Monte Carlo, {cfg.n_simulations} paths, fixed-fractional "
                    f"{cfg.risk_pct}% risk.",
        caveats=["A Monte Carlo describes the distribution of outcomes implied by its own "
                 "assumptions. It is not a prediction and contains no information about "
                 "the future.",
                 "i.i.d. resampling understates real dispersion because trades cluster."],
    ).to_dict()
    return result
