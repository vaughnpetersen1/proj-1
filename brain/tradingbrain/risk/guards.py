"""Trading safety guards.

Live trading is OFF unless an environment variable explicitly enables it AND the
caller passes an explicit confirmation token. There is no broker implementation
in this repository; this module exists so that any future one has a single gate
it must pass through, and so the UI can show the operator the current state.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from ..config import SETTINGS, Settings


class LiveTradingBlocked(RuntimeError):
    pass


@dataclasses.dataclass
class TradingGuards:
    settings: Settings = dataclasses.field(default_factory=lambda: SETTINGS)
    realised_pnl_today: float = 0.0
    account_equity: float = 0.0

    # -- state -------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "mode": "LIVE" if self.settings.live_trading_enabled else "PAPER",
            "live_trading_enabled": self.settings.live_trading_enabled,
            "paper_trading_enabled": self.settings.paper_trading_enabled,
            "kill_switch_engaged": self.settings.kill_switch,
            "max_daily_loss_pct": self.settings.max_daily_loss_pct,
            "max_position_pct": self.settings.max_position_pct,
            "max_portfolio_exposure_pct": self.settings.max_portfolio_exposure_pct,
            "max_order_notional": self.settings.max_order_notional,
            "broker_connected": False,
            "note": ("No broker integration exists in this build. Live trading cannot be "
                     "enabled by configuration alone: a broker adapter would additionally "
                     "have to pass every check in `check_order`, including an explicit "
                     "per-order confirmation token."),
            "as_of": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }

    # -- the gate ----------------------------------------------------------
    def check_order(self, order: dict[str, Any], *, confirmed: bool = False,
                    live: bool = False) -> dict[str, Any]:
        """Returns a decision dict. Never places anything -- nothing here can."""
        problems: list[str] = []
        if live:
            if not self.settings.live_trading_enabled:
                problems.append("live trading is disabled (BRAIN_LIVE_TRADING_ENABLED)")
            if self.settings.kill_switch:
                problems.append("kill switch is engaged (BRAIN_KILL_SWITCH)")
            if not confirmed:
                problems.append("explicit per-order confirmation was not supplied")
            problems.append("no broker adapter is implemented in this build")

        notional = abs(order.get("shares", 0) * order.get("price", 0.0))
        if notional > self.settings.max_order_notional:
            problems.append(f"order notional ${notional:,.2f} exceeds the "
                            f"${self.settings.max_order_notional:,.2f} cap")
        if self.account_equity:
            pct = notional / self.account_equity * 100.0
            if pct > self.settings.max_position_pct:
                problems.append(f"position would be {pct:.1f}% of equity, above the "
                                f"{self.settings.max_position_pct}% cap")
            loss_pct = -self.realised_pnl_today / self.account_equity * 100.0
            if loss_pct >= self.settings.max_daily_loss_pct:
                problems.append(f"daily loss {loss_pct:.2f}% has reached the "
                                f"{self.settings.max_daily_loss_pct}% limit; "
                                "no new risk today")
        return {"allowed": not problems, "mode": "LIVE" if live else "PAPER",
                "problems": problems, "order": order}


GUARDS = TradingGuards()
