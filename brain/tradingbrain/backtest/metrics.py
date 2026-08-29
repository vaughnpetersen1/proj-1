"""Performance metrics.

Every metric the brief asks for, computed from the trade list and the daily
equity curve. Ratios that need a risk-free rate use 0% and say so; metrics that
need more trades than exist return ``None`` rather than a misleading number.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any, Sequence

import numpy as np

TRADING_DAYS = 252


@dataclasses.dataclass
class PerformanceMetrics:
    data: dict[str, Any]

    def __getitem__(self, k: str) -> Any:
        return self.data[k]

    def to_dict(self) -> dict[str, Any]:
        return self.data


def _streaks(flags: Sequence[bool]) -> tuple[int, int]:
    best_w = best_l = cur_w = cur_l = 0
    for f in flags:
        if f:
            cur_w, cur_l = cur_w + 1, 0
        else:
            cur_l, cur_w = cur_l + 1, 0
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    return best_w, best_l


def drawdown_series(equity: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(peak > 0, (equity / peak - 1.0) * 100.0, 0.0)


def compute_metrics(trades: Sequence[Any], curve: Sequence[dict[str, Any]],
                    starting_equity: float) -> dict[str, Any]:
    n = len(trades)
    pnls = np.array([t.pnl for t in trades], dtype=float) if n else np.array([])
    rs = np.array([t.r_multiple for t in trades], dtype=float) if n else np.array([])
    wins = pnls > 0
    losses = pnls < 0

    eq = np.array([c["equity"] for c in curve], dtype=float) if curve else np.array([starting_equity])
    dd = drawdown_series(eq)
    daily_ret = np.diff(eq) / np.maximum(eq[:-1], 1e-9) if len(eq) > 1 else np.array([])
    years = len(eq) / TRADING_DAYS if len(eq) else 0.0

    total_return = (eq[-1] / starting_equity - 1.0) * 100.0 if len(eq) and starting_equity else 0.0
    cagr = ((eq[-1] / starting_equity) ** (1 / years) - 1.0) * 100.0 \
        if years > 0.15 and starting_equity > 0 and eq[-1] > 0 else None

    gross_win = float(pnls[wins].sum()) if wins.any() else 0.0
    gross_loss = float(-pnls[losses].sum()) if losses.any() else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_win > 0 else None)

    sd = float(np.std(daily_ret, ddof=1)) if len(daily_ret) > 2 else 0.0
    sharpe = (float(np.mean(daily_ret)) / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else None
    downside = daily_ret[daily_ret < 0]
    dsd = float(np.std(downside, ddof=1)) if len(downside) > 2 else 0.0
    sortino = (float(np.mean(daily_ret)) / dsd * math.sqrt(TRADING_DAYS)) if dsd > 0 else None
    max_dd = float(dd.min()) if len(dd) else 0.0
    calmar = (cagr / abs(max_dd)) if (cagr is not None and max_dd < 0) else None

    exposure = float(np.mean([c.get("exposure_pct", 0.0) for c in curve])) if curve else 0.0
    max_w, max_l = _streaks(list(wins)) if n else (0, 0)
    holds = np.array([t.bars_held for t in trades], dtype=float) if n else np.array([])

    def q(a: np.ndarray, p: float) -> float | None:
        return float(np.percentile(a, p)) if len(a) else None

    return {
        # --- trade statistics -------------------------------------------
        "n_trades": n,
        "n_wins": int(wins.sum()),
        "n_losses": int(losses.sum()),
        "n_scratch": int(n - wins.sum() - losses.sum()),
        "win_rate_pct": float(wins.mean() * 100) if n else float("nan"),
        "loss_rate_pct": float(losses.mean() * 100) if n else float("nan"),
        "avg_win": float(pnls[wins].mean()) if wins.any() else 0.0,
        "avg_loss": float(pnls[losses].mean()) if losses.any() else 0.0,
        "win_loss_ratio": (float(pnls[wins].mean() / abs(pnls[losses].mean()))
                           if wins.any() and losses.any() else None),
        "expectancy_dollars": float(pnls.mean()) if n else float("nan"),
        "expectancy_r": float(rs.mean()) if n else float("nan"),
        "median_r": float(np.median(rs)) if n else float("nan"),
        "stdev_r": float(np.std(rs, ddof=1)) if n > 1 else float("nan"),
        "profit_factor": profit_factor,
        "best_trade": float(pnls.max()) if n else None,
        "worst_trade": float(pnls.min()) if n else None,
        "best_trade_r": float(rs.max()) if n else None,
        "worst_trade_r": float(rs.min()) if n else None,
        "max_consecutive_wins": max_w,
        "max_consecutive_losses": max_l,
        "avg_hold_bars": float(holds.mean()) if n else None,
        "median_hold_bars": float(np.median(holds)) if n else None,
        "avg_mae_r": float(np.mean([t.mae_r for t in trades])) if n else None,
        "avg_mfe_r": float(np.mean([t.mfe_r for t in trades])) if n else None,
        "total_commission": float(sum(t.commission for t in trades)) if n else 0.0,
        "r_distribution": {
            "p05": q(rs, 5), "p25": q(rs, 25), "p50": q(rs, 50),
            "p75": q(rs, 75), "p95": q(rs, 95),
        },
        # --- equity-curve statistics -------------------------------------
        "starting_equity": starting_equity,
        "ending_equity": float(eq[-1]) if len(eq) else starting_equity,
        "total_return_pct": total_return,
        "cagr_pct": cagr,
        "max_drawdown_pct": max_dd,
        "avg_drawdown_pct": float(dd[dd < 0].mean()) if (dd < 0).any() else 0.0,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "exposure_pct": exposure,
        "days_simulated": len(eq),
        "years_simulated": round(years, 2),
        # --- honesty ------------------------------------------------------
        "notes": [
            "Sharpe and Sortino assume a 0% risk-free rate and are computed from daily "
            "equity changes, not from trade returns.",
            "Metrics with fewer than 30 trades behind them are dominated by noise; treat "
            "them as descriptive of this simulation only.",
        ] + ([f"SMALL SAMPLE: only {n} trades."] if 0 < n < 30 else [])
          + (["NO TRADES: the strategy produced no entries over this period. Check the "
              "rejection breakdown to see which filter removed the candidates."] if n == 0 else []),
    }
