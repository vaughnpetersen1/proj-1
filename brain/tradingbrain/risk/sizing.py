"""Position sizing and risk arithmetic.

Deterministic arithmetic, no modelling. Every function states what it assumes
and refuses to return a size when the inputs are incoherent (stop above entry on
a long, zero risk per share) instead of silently producing a huge number.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

from ..config import SETTINGS, Settings


@dataclasses.dataclass
class PositionPlan:
    ok: bool
    reason: str = ""
    direction: str = "long"
    entry: float = 0.0
    stop: float = 0.0
    target: float | None = None
    risk_per_share: float = 0.0
    risk_per_share_pct: float = 0.0
    account_equity: float = 0.0
    risk_pct: float = 0.0
    max_risk_dollars: float = 0.0
    shares: int = 0
    position_value: float = 0.0
    position_pct_of_equity: float = 0.0
    actual_risk_dollars: float = 0.0
    actual_risk_pct: float = 0.0
    potential_profit: float | None = None
    reward_risk: float | None = None
    constraints_applied: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def position_size(account_equity: float, risk_pct: float, entry: float, stop: float,
                  target: float | None = None, direction: str = "long",
                  max_position_pct: float | None = None,
                  available_cash: float | None = None,
                  adr_pct: float | None = None,
                  max_stop_adr_multiple: float | None = None,
                  settings: Settings = SETTINGS) -> PositionPlan:
    p = PositionPlan(ok=False, direction=direction, entry=entry, stop=stop, target=target,
                     account_equity=account_equity, risk_pct=risk_pct)
    if account_equity <= 0:
        p.reason = "account equity must be positive"
        return p
    if entry <= 0:
        p.reason = "entry price must be positive"
        return p
    if risk_pct <= 0 or risk_pct > 100:
        p.reason = "risk percentage must be in (0, 100]"
        return p
    if direction == "long" and stop >= entry:
        p.reason = f"long stop {stop} must be below entry {entry}"
        return p
    if direction == "short" and stop <= entry:
        p.reason = f"short stop {stop} must be above entry {entry}"
        return p

    rps = abs(entry - stop)
    p.risk_per_share = rps
    p.risk_per_share_pct = rps / entry * 100.0
    p.max_risk_dollars = account_equity * risk_pct / 100.0

    if adr_pct and max_stop_adr_multiple:
        limit = max_stop_adr_multiple * adr_pct
        if p.risk_per_share_pct > limit:
            p.warnings.append(
                f"Stop is {p.risk_per_share_pct:.2f}% wide, beyond {max_stop_adr_multiple}x "
                f"ADR ({limit:.2f}%). Kullamagi's public rule is to skip this trade rather "
                "than size down.")

    shares = math.floor(p.max_risk_dollars / rps)
    if shares < 1:
        p.reason = (f"risk budget ${p.max_risk_dollars:,.2f} is smaller than the "
                    f"${rps:,.2f} risked per share; the position rounds to zero")
        return p

    cap_pct = max_position_pct if max_position_pct is not None else settings.max_position_pct
    by_pos = math.floor(account_equity * cap_pct / 100.0 / entry)
    if by_pos < shares:
        shares = by_pos
        p.constraints_applied.append(f"max position size {cap_pct}% of equity")
    if available_cash is not None:
        by_cash = math.floor(max(available_cash, 0) / entry)
        if by_cash < shares:
            shares = by_cash
            p.constraints_applied.append("available cash")
    if shares < 1:
        p.reason = "constraints reduce the position to zero shares"
        return p

    p.shares = int(shares)
    p.position_value = shares * entry
    p.position_pct_of_equity = p.position_value / account_equity * 100.0
    p.actual_risk_dollars = shares * rps
    p.actual_risk_pct = p.actual_risk_dollars / account_equity * 100.0

    # The order-notional cap is a LIVE-ORDER guard, not a sizing rule: silently
    # shrinking a research position size to fit it would misreport the strategy.
    # It is surfaced as a warning here and enforced in risk.guards.check_order.
    if p.position_value > settings.max_order_notional:
        p.warnings.append(
            f"Position notional ${p.position_value:,.0f} exceeds the "
            f"${settings.max_order_notional:,.0f} per-order cap "
            "(BRAIN_MAX_ORDER_NOTIONAL). Sizing is unchanged; a live order of this "
            "size would be blocked by the trading guards.")

    if target is not None:
        per_share = (target - entry) if direction == "long" else (entry - target)
        p.potential_profit = per_share * shares
        p.reward_risk = (per_share / rps) if rps else None
        if p.reward_risk is not None and p.reward_risk < 1:
            p.warnings.append(
                f"Reward-to-risk is {p.reward_risk:.2f}: the target is closer than the stop.")
    p.ok = True
    return p


def option_position_size(account_equity: float, risk_pct: float, premium: float,
                         contracts_multiplier: int = 100,
                         max_loss_fraction_of_premium: float = 1.0,
                         settings: Settings = SETTINGS) -> dict[str, Any]:
    """Size a long-option position.

    Assumes the risk is the premium paid times ``max_loss_fraction_of_premium``.
    That is exact for a long option held to expiry and conservative for a trade
    exited earlier; it is WRONG for any short-option or spread position, which
    this function refuses rather than mis-sizing.
    """
    if premium <= 0:
        return {"ok": False, "reason": "premium must be positive"}
    if account_equity <= 0 or risk_pct <= 0:
        return {"ok": False, "reason": "equity and risk percentage must be positive"}
    cost = premium * contracts_multiplier
    risk_budget = account_equity * risk_pct / 100.0
    risk_per_contract = cost * max_loss_fraction_of_premium
    n = math.floor(risk_budget / risk_per_contract)
    capped = False
    if n * cost > settings.max_order_notional:
        n = math.floor(settings.max_order_notional / cost)
        capped = True
    if n < 1:
        return {"ok": False,
                "reason": (f"one contract costs ${cost:,.2f} and risks "
                           f"${risk_per_contract:,.2f}, more than the "
                           f"${risk_budget:,.2f} risk budget")}
    return {
        "ok": True, "contracts": int(n), "premium_per_contract": premium,
        "cost_per_contract": cost, "total_cost": n * cost,
        "max_loss": n * risk_per_contract,
        "max_loss_pct_of_equity": n * risk_per_contract / account_equity * 100.0,
        "capped_by_max_order_notional": capped,
        "assumptions": [
            "Maximum loss is the premium paid; true for a long option held to expiry.",
            "Assignment, early exercise, and spread/short structures are NOT modelled.",
            "Bid/ask spread and commissions are not included in the premium shown.",
        ],
    }


def risk_reward(entry: float, stop: float, target: float,
                direction: str = "long") -> dict[str, Any]:
    rps = abs(entry - stop)
    per_share = (target - entry) if direction == "long" else (entry - target)
    if rps <= 0:
        return {"ok": False, "reason": "stop equals entry: risk per share is zero"}
    rr = per_share / rps
    return {
        "ok": True, "risk_per_share": rps, "reward_per_share": per_share,
        "reward_risk": rr, "stop_pct": rps / entry * 100.0,
        "target_pct": per_share / entry * 100.0,
        "breakeven_win_rate_pct": 100.0 / (1.0 + rr) if rr > 0 else None,
        "note": ("Breakeven win rate is the fraction of trades that must reach the target "
                 "for this reward/risk to break even before costs. It says nothing about "
                 "whether the setup achieves it."),
    }


def portfolio_check(positions: list[dict[str, Any]], account_equity: float,
                    settings: Settings = SETTINGS) -> dict[str, Any]:
    """Aggregate exposure and open risk across positions."""
    total_value = sum(p.get("shares", 0) * p.get("price", 0.0) for p in positions)
    total_risk = sum(max(0.0, (p.get("price", 0.0) - p.get("stop", 0.0))) * p.get("shares", 0)
                     for p in positions)
    breaches = []
    exposure_pct = total_value / account_equity * 100.0 if account_equity else 0.0
    if exposure_pct > settings.max_portfolio_exposure_pct:
        breaches.append(f"portfolio exposure {exposure_pct:.1f}% exceeds "
                        f"{settings.max_portfolio_exposure_pct}%")
    for p in positions:
        v = p.get("shares", 0) * p.get("price", 0.0)
        pct = v / account_equity * 100.0 if account_equity else 0.0
        if pct > settings.max_position_pct:
            breaches.append(f"{p.get('symbol')} is {pct:.1f}% of equity, above "
                            f"{settings.max_position_pct}%")
    return {
        "positions": len(positions),
        "total_value": total_value,
        "exposure_pct": exposure_pct,
        "open_risk_dollars": total_risk,
        "open_risk_pct": total_risk / account_equity * 100.0 if account_equity else 0.0,
        "breaches": breaches,
        "ok": not breaches,
    }
