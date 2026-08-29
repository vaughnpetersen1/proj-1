"""Sector and theme ranking.

The brief is explicit: do not rank by today's percentage gain. So each group is
scored from six measurements over different horizons, and the individual
component scores are always shown next to the composite so a ranking can be
argued with rather than accepted.

The weights are configurable and are NOT claimed to be optimal. They are a
starting hypothesis; ``research.hypothesis`` can test alternatives.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Iterable, Sequence

import numpy as np

from ..data.panel import Panel, build_panel
from ..data.providers.registry import HUB, DataHub
from ..indicators.core import sma
from ..provenance import Claim, DataOrigin, EvidenceClass

DEFAULT_WEIGHTS: dict[str, float] = {
    "return_1m": 0.20,
    "return_3m": 0.25,
    "return_6m": 0.10,
    "pct_above_50sma": 0.20,
    "pct_near_52w_high": 0.15,
    "median_member_rs": 0.10,
}


@dataclasses.dataclass
class GroupRank:
    name: str
    kind: str                       # "sector" | "theme"
    members: list[str]
    members_with_data: int
    components: dict[str, float]
    component_percentiles: dict[str, float]
    score: float
    rank: int = 0
    leaders: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _safe(a: np.ndarray) -> float:
    a = a[np.isfinite(a)]
    return float(np.median(a)) if len(a) else float("nan")


class SectorRanker:
    def __init__(self, hub: DataHub = HUB, panel: Panel | None = None,
                 weights: dict[str, float] | None = None) -> None:
        self.hub = hub
        self.panel = panel if panel is not None else build_panel(hub=hub)
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v / total for k, v in self.weights.items()}
        self._cache: dict[str, np.ndarray] = {}

    # -- per-symbol measurements as of a row -------------------------------
    def _returns(self, bars: int) -> np.ndarray:
        key = f"ret{bars}"
        if key not in self._cache:
            self._cache[key] = self.panel.returns(bars)
        return self._cache[key]

    def _above_50(self) -> np.ndarray:
        if "above50" not in self._cache:
            n_dates, n_syms = self.panel.close.shape
            out = np.full(self.panel.close.shape, np.nan)
            for j in range(n_syms):
                col = self.panel.close[:, j]
                ok = np.isfinite(col)
                if ok.sum() < 55:
                    continue
                m = np.full(n_dates, np.nan)
                m[ok] = sma(col[ok], 50)
                out[:, j] = np.where(np.isfinite(m), (col > m).astype(float), np.nan)
            self._cache["above50"] = out
        return self._cache["above50"]

    def _near_high(self, lookback: int = 252, tol: float = 0.05) -> np.ndarray:
        if "nearhigh" not in self._cache:
            from ..indicators.core import rolling_max
            n_dates, n_syms = self.panel.close.shape
            out = np.full(self.panel.close.shape, np.nan)
            for j in range(n_syms):
                col = self.panel.high[:, j]
                cl = self.panel.close[:, j]
                ok = np.isfinite(col)
                if ok.sum() < lookback // 2:
                    continue
                hi = np.full(n_dates, np.nan)
                hi[ok] = rolling_max(col[ok], min(lookback, int(ok.sum())))
                with np.errstate(invalid="ignore"):
                    out[:, j] = np.where(np.isfinite(hi) & (hi > 0),
                                         (cl >= hi * (1 - tol)).astype(float), np.nan)
            self._cache["nearhigh"] = out
        return self._cache["nearhigh"]

    def _rs_percentile(self, bars: int = 63) -> np.ndarray:
        key = f"rs{bars}"
        if key not in self._cache:
            self._cache[key] = self.panel.rank_percentile(self._returns(bars))
        return self._cache[key]

    # -- ranking ------------------------------------------------------------
    def rank(self, groups: dict[str, Sequence[str]], kind: str,
             as_of: dt.date | None = None, top_members: int = 5) -> list[GroupRank]:
        row = self.panel.row(as_of) if as_of else len(self.panel.dates) - 1
        if row < 0:
            return []
        r1, r3, r6 = (self._returns(21)[row], self._returns(63)[row],
                      self._returns(126)[row])
        a50 = self._above_50()[row]
        nh = self._near_high()[row]
        rs = self._rs_percentile()[row]

        raw: list[GroupRank] = []
        for name, members in groups.items():
            cols = [self.panel.col(m) for m in members if m.upper() in self.panel.symbols]
            if not cols:
                continue
            idx = np.array(cols)
            comp = {
                "return_1m": _safe(r1[idx]),
                "return_3m": _safe(r3[idx]),
                "return_6m": _safe(r6[idx]),
                "pct_above_50sma": float(np.nanmean(a50[idx]) * 100)
                if np.isfinite(a50[idx]).any() else float("nan"),
                "pct_near_52w_high": float(np.nanmean(nh[idx]) * 100)
                if np.isfinite(nh[idx]).any() else float("nan"),
                "median_member_rs": _safe(rs[idx]),
            }
            leaders = sorted(
                ({"symbol": self.panel.symbols[j],
                  "return_3m": round(float(r3[j]), 2) if np.isfinite(r3[j]) else None,
                  "rs_percentile": round(float(rs[j]), 1) if np.isfinite(rs[j]) else None}
                 for j in idx),
                key=lambda d: (d["rs_percentile"] is None, -(d["rs_percentile"] or 0)))[:top_members]
            raw.append(GroupRank(name=name, kind=kind, members=list(members),
                                 members_with_data=len(idx), components=comp,
                                 component_percentiles={}, score=0.0, leaders=leaders))

        # convert each component to a cross-group percentile before weighting, so a
        # single high-variance component cannot dominate the composite by scale alone
        for key in DEFAULT_WEIGHTS:
            vals = np.array([g.components.get(key, np.nan) for g in raw], float)
            ok = np.isfinite(vals)
            pct = np.full(len(vals), np.nan)
            if ok.sum() >= 2:
                order = np.argsort(np.argsort(vals[ok]))
                pct[ok] = order / (ok.sum() - 1) * 100.0
            for g, p in zip(raw, pct):
                g.component_percentiles[key] = float(p) if np.isfinite(p) else float("nan")

        for g in raw:
            num = den = 0.0
            for key, w in self.weights.items():
                p = g.component_percentiles.get(key, float("nan"))
                if np.isfinite(p):
                    num += w * p
                    den += w
            g.score = round(num / den, 2) if den > 0 else float("nan")

        raw.sort(key=lambda g: (-(g.score if np.isfinite(g.score) else -1), g.name))
        for i, g in enumerate(raw, 1):
            g.rank = i
        return raw

    def as_of_date(self, as_of: dt.date | None = None) -> str:
        row = self.panel.row(as_of) if as_of else len(self.panel.dates) - 1
        return self.panel.dates[row].isoformat() if row >= 0 else ""

    def claim(self, ranks: list[GroupRank], kind: str, as_of: str) -> Claim:
        top = ", ".join(f"{g.name} ({g.score:.0f})" for g in ranks[:3])
        return Claim(
            statement=f"Leading {kind}s as of {as_of}: {top}.",
            evidence=EvidenceClass.LIVE_MARKET_OBSERVATION,
            value=[g.name for g in ranks[:3]],
            sample_size=len(ranks),
            period=as_of,
            data_source=self.panel.provider,
            data_origin=DataOrigin(self.panel.origin)
            if self.panel.origin in DataOrigin.__members__ else DataOrigin.UNKNOWN,
            methodology=("Six measurements (1m/3m/6m median member return, share of members "
                         "above their 50 SMA, share within 5% of a 52-week high, median "
                         "member relative-strength percentile) converted to cross-group "
                         "percentiles and combined with configurable weights "
                         f"{ {k: round(v,3) for k,v in self.weights.items()} }."),
            caveats=["The weights are a starting hypothesis, not a validated result.",
                     "Group membership is a present-day list; a sector's historical "
                     "constituents differed."],
        )


def rank_sectors(as_of: dt.date | None = None, hub: DataHub = HUB,
                 panel: Panel | None = None,
                 weights: dict[str, float] | None = None) -> dict[str, Any]:
    r = SectorRanker(hub, panel, weights)
    smap = hub.sector_map()
    groups: dict[str, list[str]] = {}
    for sym, sector in smap.items():
        groups.setdefault(sector, []).append(sym)
    ranks = r.rank(groups, "sector", as_of)
    as_of_str = r.as_of_date(as_of)
    return {"as_of": as_of_str, "kind": "sector",
            "ranks": [g.to_dict() for g in ranks],
            "weights": r.weights,
            "claim": r.claim(ranks, "sector", as_of_str).to_dict()}


def rank_themes(as_of: dt.date | None = None, hub: DataHub = HUB,
                panel: Panel | None = None,
                weights: dict[str, float] | None = None) -> dict[str, Any]:
    r = SectorRanker(hub, panel, weights)
    ranks = r.rank(hub.themes(), "theme", as_of)
    as_of_str = r.as_of_date(as_of)
    return {"as_of": as_of_str, "kind": "theme",
            "ranks": [g.to_dict() for g in ranks],
            "weights": r.weights,
            "claim": r.claim(ranks, "theme", as_of_str).to_dict()}


def rank_group_members(members: Iterable[str], as_of: dt.date | None = None,
                       hub: DataHub = HUB, panel: Panel | None = None) -> list[dict[str, Any]]:
    r = SectorRanker(hub, panel)
    row = r.panel.row(as_of) if as_of else len(r.panel.dates) - 1
    rs = r._rs_percentile()[row]
    r1, r3 = r._returns(21)[row], r._returns(63)[row]
    a50 = r._above_50()[row]
    out = []
    for m in members:
        if m.upper() not in r.panel.symbols:
            continue
        j = r.panel.col(m)
        out.append({"symbol": m.upper(),
                    "rs_percentile": round(float(rs[j]), 1) if np.isfinite(rs[j]) else None,
                    "return_1m": round(float(r1[j]), 2) if np.isfinite(r1[j]) else None,
                    "return_3m": round(float(r3[j]), 2) if np.isfinite(r3[j]) else None,
                    "above_50sma": bool(a50[j]) if np.isfinite(a50[j]) else None})
    out.sort(key=lambda d: (d["rs_percentile"] is None, -(d["rs_percentile"] or 0)))
    return out


def sector_percentile_map(as_of: dt.date | None = None, hub: DataHub = HUB,
                          panel: Panel | None = None) -> dict[str, float]:
    """symbol -> percentile rank of the symbol's sector. Used by the setup filter."""
    res = rank_sectors(as_of, hub, panel)
    ranks = res["ranks"]
    if not ranks:
        return {}
    n = len(ranks)
    by_sector = {g["name"]: 100.0 * (n - g["rank"]) / max(n - 1, 1) for g in ranks}
    return {sym: by_sector.get(sector, float("nan"))
            for sym, sector in hub.sector_map().items()}
