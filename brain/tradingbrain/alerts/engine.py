"""Alert engine.

The evaluation half is real: conditions are checked against the same engines the
rest of the system uses, and firings are persisted with their evidence.

The delivery half is deliberately a declared interface with one working channel
(in-app). Email/Discord/Telegram/SMS/broker channels are described, not faked --
a channel that silently does nothing is worse than one that says it is not
configured.
"""

from __future__ import annotations

import abc
import dataclasses
import datetime as dt
from typing import Any, Iterable

import numpy as np

from ..data.providers.registry import HUB, DataHub
from ..db.store import STORE, Store
from ..indicators.core import sma

ALERT_KINDS: dict[str, str] = {
    "approaching_breakout": "A tracked symbol is within N% of its consolidation high.",
    "breakout": "A tracked symbol closed above its consolidation high today.",
    "volume_confirmation": "Relative volume exceeded a threshold on a tracked symbol.",
    "regime_change": "The market regime label or the index MA filter state changed.",
    "sector_leadership_change": "A new sector took the top rank.",
    "stop_triggered": "Price traded at or below a stop level you registered.",
    "target_reached": "Price traded at or above a target level you registered.",
    "setup_detected": "A scan found a symbol matching the strategy definition.",
}


class Channel(abc.ABC):
    name: str = "abstract"
    configured: bool = False

    @abc.abstractmethod
    def send(self, event: dict[str, Any]) -> dict[str, Any]: ...

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "configured": self.configured}


class InAppChannel(Channel):
    """The only implemented channel: rows in ``alert_events``, read by the UI."""
    name = "inapp"
    configured = True

    def __init__(self, store: Store = STORE) -> None:
        self.store = store

    def send(self, event: dict[str, Any]) -> dict[str, Any]:
        eid = self.store.fire_alert(event.get("alert_id"), event["kind"],
                                    event.get("symbol"), event)
        return {"delivered": True, "channel": self.name, "event_id": eid}


class UnconfiguredChannel(Channel):
    """A named but unimplemented delivery route. Reports honestly, never pretends."""

    def __init__(self, name: str, requirement: str) -> None:
        self.name = name
        self.requirement = requirement
        self.configured = False

    def send(self, event: dict[str, Any]) -> dict[str, Any]:
        return {"delivered": False, "channel": self.name,
                "reason": f"{self.name} delivery is not implemented. {self.requirement}"}

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "configured": False, "requirement": self.requirement}


CHANNELS: dict[str, Channel] = {
    "inapp": InAppChannel(),
    "browser": UnconfiguredChannel(
        "browser", "Needs a service worker and a push subscription from the UI."),
    "email": UnconfiguredChannel("email", "Needs SMTP settings or an email API key."),
    "discord": UnconfiguredChannel("discord", "Needs a DISCORD_WEBHOOK_URL."),
    "telegram": UnconfiguredChannel("telegram", "Needs TELEGRAM_BOT_TOKEN and a chat id."),
    "sms": UnconfiguredChannel("sms", "Needs a Twilio (or equivalent) account and number."),
    "broker": UnconfiguredChannel(
        "broker", "No broker adapter exists. Order routing would additionally have to pass "
                  "every check in tradingbrain.risk.guards, including per-order confirmation."),
}


@dataclasses.dataclass
class AlertEngine:
    hub: DataHub = dataclasses.field(default_factory=lambda: HUB)
    store: Store = dataclasses.field(default_factory=lambda: STORE)

    # -- CRUD --------------------------------------------------------------
    def create(self, kind: str, condition: dict[str, Any], symbol: str | None = None,
               channels: Iterable[str] = ("inapp",)) -> dict[str, Any]:
        if kind not in ALERT_KINDS:
            return {"ok": False, "reason": f"unknown alert kind {kind!r}",
                    "known": sorted(ALERT_KINDS)}
        bad = [c for c in channels if c not in CHANNELS]
        if bad:
            return {"ok": False, "reason": f"unknown channel(s) {bad}",
                    "known": sorted(CHANNELS)}
        aid = self.store.add_alert(kind, condition, symbol.upper() if symbol else None,
                                   channels)
        unconfigured = [c for c in channels if not CHANNELS[c].configured]
        return {"ok": True, "id": aid, "kind": kind, "symbol": symbol,
                "channels": list(channels),
                "warning": (f"Channels {unconfigured} are not implemented; firings will be "
                            "recorded in-app only." if unconfigured else None)}

    # -- evaluation --------------------------------------------------------
    def evaluate(self, as_of: dt.date | None = None) -> dict[str, Any]:
        fired: list[dict[str, Any]] = []
        checked = 0
        for alert in self.store.alerts(active_only=True):
            checked += 1
            hit = self._check(alert, as_of)
            if hit is None:
                continue
            payload = {"alert_id": alert["id"], "kind": alert["kind"],
                       "symbol": alert.get("symbol"), **hit,
                       "fired_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
            deliveries = [CHANNELS[c].send(payload) if c != "inapp" else CHANNELS["inapp"].send(payload)
                          for c in (alert.get("channels") or ["inapp"])]
            payload["deliveries"] = deliveries
            fired.append(payload)
        return {"as_of": (as_of or dt.date.today()).isoformat(), "checked": checked,
                "fired": len(fired), "events": fired,
                "channels": [c.describe() for c in CHANNELS.values()]}

    def _check(self, alert: dict[str, Any], as_of: dt.date | None) -> dict[str, Any] | None:
        kind, cond, sym = alert["kind"], alert.get("condition") or {}, alert.get("symbol")
        try:
            if kind in ("approaching_breakout", "breakout", "volume_confirmation",
                        "stop_triggered", "target_reached"):
                if not sym:
                    return None
                s = self.hub.daily(sym, end=as_of)
                if len(s) < 60:
                    return None
                i = len(s) - 1
                close, high, low = float(s.close[i]), float(s.high[i]), float(s.low[i])
                if kind == "stop_triggered":
                    level = float(cond.get("level", 0))
                    return ({"detail": f"{sym} traded to {low} at or below stop {level}",
                             "price": low, "level": level} if low <= level else None)
                if kind == "target_reached":
                    level = float(cond.get("level", 1e18))
                    return ({"detail": f"{sym} traded to {high} at or above target {level}",
                             "price": high, "level": level} if high >= level else None)
                from ..strategy.evaluator import SymbolContext
                from ..strategy.library import get_strategy
                spec = get_strategy(cond.get("strategy_key", "sar_v1_1_adr_stop"))
                ctx = SymbolContext(s, spec)
                if kind == "volume_confirmation":
                    thr = float(cond.get("min_rvol", 2.0))
                    rv = float(ctx.rvol[i]) if np.isfinite(ctx.rvol[i]) else 0.0
                    return ({"detail": f"{sym} relative volume {rv:.2f} >= {thr}",
                             "rvol": rv} if rv >= thr else None)
                base = ctx.cons.at(i - 1 if ctx.breakouts[i] else i)
                if base is None:
                    return None
                if kind == "breakout":
                    return ({"detail": f"{sym} closed {close} above base high {base.high:.2f}",
                             "level": base.high, "price": close}
                            if bool(ctx.breakouts[i]) else None)
                pct = (base.high / close - 1) * 100.0
                thr = float(cond.get("within_pct", 3.0))
                return ({"detail": f"{sym} is {pct:.2f}% below its base high "
                                   f"{base.high:.2f}", "pct_to_breakout": round(pct, 2),
                         "level": base.high} if 0 <= pct <= thr else None)

            if kind == "regime_change":
                from ..regime.classifier import classify_market
                r = classify_market(as_of, self.hub, with_breadth=False)
                prev = self.store.kv_get("last_regime")
                now = {"label": r.label, "filter_pass": r.filter_pass}
                self.store.kv_set("last_regime", now)
                if prev and prev != now:
                    return {"detail": f"Regime changed from {prev} to {now}",
                            "previous": prev, "current": now}
                return None

            if kind == "sector_leadership_change":
                from ..sectors.ranking import rank_sectors
                r = rank_sectors(as_of, self.hub)
                top = r["ranks"][0]["name"] if r["ranks"] else None
                prev = self.store.kv_get("top_sector")
                self.store.kv_set("top_sector", top)
                if prev and top and prev != top:
                    return {"detail": f"Sector leadership moved from {prev} to {top}",
                            "previous": prev, "current": top}
                return None

            if kind == "setup_detected":
                from ..scanner.scan import ScanConfig, run_scan
                res = run_scan(ScanConfig(as_of=as_of, limit=10,
                                          strategy_key=cond.get("strategy_key",
                                                                "sar_v1_1_adr_stop")),
                               self.hub)
                hits = [c for c in res.get("results", [])
                        if c["score"] >= float(cond.get("min_score", 70))]
                return ({"detail": f"{len(hits)} setup(s) scored at or above "
                                   f"{cond.get('min_score', 70)}",
                         "symbols": [h["symbol"] for h in hits],
                         "top": hits[:5]} if hits else None)
        except Exception as exc:  # noqa: BLE001 - an alert must not break the loop
            return {"detail": f"evaluation error: {type(exc).__name__}: {exc}", "error": True}
        return None


def create_alert(kind: str, condition: dict[str, Any], symbol: str | None = None,
                 channels: Iterable[str] = ("inapp",)) -> dict[str, Any]:
    return AlertEngine().create(kind, condition, symbol, channels)


def evaluate_alerts(as_of: dt.date | None = None) -> dict[str, Any]:
    return AlertEngine().evaluate(as_of)


def list_alerts() -> dict[str, Any]:
    return {"alerts": STORE.alerts(active_only=False),
            "kinds": ALERT_KINDS,
            "channels": [c.describe() for c in CHANNELS.values()]}


def alert_history(limit: int = 100) -> list[dict[str, Any]]:
    return STORE.alert_events(limit)
