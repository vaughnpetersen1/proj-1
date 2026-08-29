"""Persist the derived market context the screener filters on.

Market regime and sector/theme rankings are computed from stored bars and then
written to ``market_regimes`` and ``sector_data``. Persisting them does two
things a recompute-on-request design cannot:

  * the SQL screener can filter on regime and sector rank without loading a
    cross-sectional panel per request, and
  * the history is kept, so "which regimes did this strategy actually trade in"
    becomes answerable instead of a guess.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from ..data.panel import build_panel
from ..data.providers.registry import HUB, DataHub
from ..marketstore.store import MARKET, MarketStore
from ..regime.classifier import classify_market
from ..sectors.ranking import rank_sectors, rank_themes


def refresh_context(as_of: dt.date | None = None, hub: DataHub = HUB,
                    store: MarketStore = MARKET,
                    with_breadth: bool = True) -> dict[str, Any]:
    t0 = time.time()
    panel = build_panel(hub=hub)
    if not panel.dates:
        return {"ok": False, "reason": "no bars available to compute context from"}
    as_of = as_of or panel.dates[-1]

    regime = classify_market(as_of, hub, with_breadth=with_breadth)
    store.save_regime(regime.as_of or as_of.isoformat(), regime.to_dict())

    sect = rank_sectors(as_of, hub, panel)
    them = rank_themes(as_of, hub, panel)
    n_s = store.save_sector_ranks(sect["as_of"], "sector", sect["ranks"])
    n_t = store.save_sector_ranks(them["as_of"], "theme", them["ranks"])

    store.kv_set("themes", hub.themes())
    store.log("info", "context",
              f"regime={regime.label} strength={regime.strength:.0f} "
              f"sectors={n_s} themes={n_t} as_of={as_of}")
    return {
        "ok": True, "as_of": as_of.isoformat(),
        "regime": {"label": regime.label, "volatility": regime.volatility_label,
                   "strength": regime.strength, "filter_pass": regime.filter_pass},
        "sectors_saved": n_s, "themes_saved": n_t,
        "leading_sectors": [g["name"] for g in sect["ranks"][:3]],
        "leading_themes": [g["name"] for g in them["ranks"][:3]],
        "seconds": round(time.time() - t0, 2),
    }


def backfill_context(start: dt.date, end: dt.date | None = None, step_days: int = 5,
                     hub: DataHub = HUB, store: MarketStore = MARKET) -> dict[str, Any]:
    """Walk the calendar computing and storing regime history.

    Sampled every ``step_days`` by default: a daily regime label barely moves and
    a full daily pass over years of history is minutes of work for no extra
    information.
    """
    from ..marketstore import calendar as cal
    panel = build_panel(hub=hub)
    end = end or (panel.dates[-1] if panel.dates else dt.date.today())
    days = cal.trading_days(start, end)[::step_days]
    saved = 0
    for d in days:
        try:
            r = classify_market(d, hub, with_breadth=False)
        except Exception:                                     # noqa: BLE001
            continue
        if r.label == "UNKNOWN":
            continue
        store.save_regime(r.as_of or d.isoformat(), r.to_dict())
        saved += 1
    return {"ok": True, "dates": len(days), "saved": saved,
            "step_days": step_days,
            "note": "breadth omitted for historical points: computing it per date over "
                    "the whole universe costs minutes and does not change the label."}
