"""Declarative strategy specification.

A strategy is **data**, never code. The backtester interprets this structure; a
user-authored strategy cannot execute anything. That is both a security
requirement (section 41 of the brief) and what makes strategies versionable,
diffable and reproducible.

Every threshold that the source material states qualitatively appears here as an
explicit parameter with a default that is a *starting point for testing*, not a
finding.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Literal


class SpecError(ValueError):
    """Raised when a strategy specification is malformed or unsafe."""


def _take(d: dict, cls):
    """Build a dataclass from a dict, rejecting unknown keys loudly."""
    if d is None:
        return cls()
    if isinstance(d, cls):
        return d
    known = set(cls.__dataclass_fields__)
    unknown = set(d) - known
    if unknown:
        raise SpecError(f"{cls.__name__}: unknown field(s) {sorted(unknown)}; "
                        f"allowed: {sorted(known)}")
    return cls(**d)


# ---------------------------------------------------------------------------

@dataclasses.dataclass
class UniverseSpec:
    symbols: list[str] | None = None       # None = every symbol the data path serves
    exclude: list[str] = dataclasses.field(default_factory=list)
    min_price: float = 5.0
    max_price: float | None = None
    min_dollar_volume: float = 3_000_000.0  # 20-day average
    min_adr_pct: float = 0.0
    max_adr_pct: float | None = None


@dataclasses.dataclass
class MarketFilter:
    """SAR: avoid breakouts when the index 10 MA is below the 20 MA.

    Stored as a rule to be *tested*, not obeyed. ``enabled=False`` is the control
    arm of that experiment.
    """
    enabled: bool = True
    symbol: str = "SPY"
    secondary_symbol: str | None = "QQQ"
    require_fast_above_slow: bool = True
    fast: int = 10
    slow: int = 20
    require_close_above_slow: bool = False
    max_drawdown_from_high_pct: float | None = None   # e.g. 10 -> skip if index >10% off high
    mode: Literal["either", "both"] = "both"


@dataclasses.dataclass
class SectorFilter:
    enabled: bool = False
    min_rank_percentile: float = 50.0     # symbol's sector must rank this high
    lookback: int = 63


@dataclasses.dataclass
class SetupSpec:
    # prior expansion
    prior_move_min_pct: float = 30.0
    prior_move_lookback: int = 63
    prior_move_max_bars: int = 60
    prior_move_min_bars: int = 3
    #: A one-day gap is not a multi-day expansion, however long the flat period
    #: before it was. Caps the share of the advance any single bar may contribute.
    prior_move_max_single_bar_share: float = 0.80
    # consolidation
    cons_min_len: int = 5
    cons_max_len: int = 40
    cons_max_depth_pct: float = 35.0
    cons_max_range_ratio: float = 1.10
    cons_max_volume_ratio: float = 1.10
    cons_require_ma_support: bool = True
    cons_ma_len: int = 20
    cons_min_close_above_ma_frac: float = 0.60
    cons_min_quality: float = 0.0
    # trend / location
    require_close_above_smas: list[int] = dataclasses.field(default_factory=lambda: [10, 20])
    require_sma_stack: list[int] = dataclasses.field(default_factory=list)  # e.g. [10,20,50]
    max_pct_above_sma20: float | None = None      # "avoid extended"
    max_adr_above_sma20: float | None = None
    min_r2_20: float | None = None                # "orderly / linear" candidate measure
    min_rs_percentile: float | None = None        # relative strength vs universe
    rs_lookback: int = 63


@dataclasses.dataclass
class EntrySpec:
    type: Literal["daily_breakout", "opening_range", "next_open"] = "daily_breakout"
    breakout_min_rvol: float = 1.5
    breakout_require_gt_prev_volume: bool = True
    breakout_min_close_position: float = 0.50
    breakout_buffer_pct: float = 0.0
    opening_range_minutes: int = 5
    #: how the fill is modelled. 'stop_at_level' assumes a resting buy-stop at the
    #: breakout level and fills intrabar; 'next_open' is the conservative choice
    #: for daily-only data and is the default.
    fill: Literal["next_open", "close", "stop_at_level"] = "next_open"


@dataclasses.dataclass
class StopSpec:
    type: Literal["low_of_day", "low_of_base", "pct", "atr", "adr"] = "low_of_day"
    value: float = 1.0        # pct for 'pct', multiple for 'atr'/'adr'
    #: Kullamagi's public rule: skip the trade if the low-of-day stop is wider
    #: than 1x ADR. Set to None to disable the skip.
    max_stop_adr_multiple: float | None = 1.0
    max_stop_pct: float | None = None


@dataclasses.dataclass
class PartialExit:
    at_r: float = 1.0
    fraction: float = 0.33
    after_days: int | None = None   # SAR/Kullamagi both describe a time-based scale-out


@dataclasses.dataclass
class TrailSpec:
    type: Literal["none", "sma", "ema", "atr", "chandelier"] = "sma"
    length: int = 10
    multiple: float = 2.0
    basis: Literal["close", "low"] = "close"
    activate_after_r: float | None = None


@dataclasses.dataclass
class ManagementSpec:
    partials: list[PartialExit] = dataclasses.field(
        default_factory=lambda: [PartialExit(at_r=1.0, fraction=0.33, after_days=3)])
    breakeven_after_r: float | None = 1.0
    breakeven_after_days: int | None = None
    trail: TrailSpec = dataclasses.field(default_factory=TrailSpec)
    max_hold_days: int | None = 120
    time_stop_days: int | None = None        # exit if not up X R after N days
    time_stop_min_r: float = 0.0
    target_r: float | None = None            # hard take-profit in R


@dataclasses.dataclass
class RiskSpec:
    account_equity: float = 100_000.0
    risk_pct: float = 1.0
    max_positions: int = 8
    max_position_pct: float = 20.0
    max_portfolio_exposure_pct: float = 100.0
    allow_pyramiding: bool = False


@dataclasses.dataclass
class CostSpec:
    slippage_bps: float = 5.0
    commission_per_share: float = 0.0
    commission_min: float = 0.0
    #: modelled as a per-bar chance the open gaps through the stop; gaps are taken
    #: from the actual data, this flag only controls whether we assume the stop
    #: fills at the stop price (False) or at the open (True, realistic).
    gap_fills_at_open: bool = True


@dataclasses.dataclass
class StrategySpec:
    name: str = "Unnamed strategy"
    version: str = "0.1"
    description: str = ""
    direction: Literal["long", "short"] = "long"
    universe: UniverseSpec = dataclasses.field(default_factory=UniverseSpec)
    market_filter: MarketFilter = dataclasses.field(default_factory=MarketFilter)
    sector_filter: SectorFilter = dataclasses.field(default_factory=SectorFilter)
    setup: SetupSpec = dataclasses.field(default_factory=SetupSpec)
    entry: EntrySpec = dataclasses.field(default_factory=EntrySpec)
    stop: StopSpec = dataclasses.field(default_factory=StopSpec)
    management: ManagementSpec = dataclasses.field(default_factory=ManagementSpec)
    risk: RiskSpec = dataclasses.field(default_factory=RiskSpec)
    costs: CostSpec = dataclasses.field(default_factory=CostSpec)
    provenance: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    notes: str = ""

    # -- (de)serialisation --------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StrategySpec":
        if not isinstance(d, dict):
            raise SpecError("strategy spec must be an object")
        d = dict(d)
        known = set(cls.__dataclass_fields__)
        unknown = set(d) - known
        if unknown:
            raise SpecError(f"StrategySpec: unknown field(s) {sorted(unknown)}")
        mgmt = d.get("management")
        if isinstance(mgmt, dict):
            mgmt = dict(mgmt)
            mgmt["partials"] = [_take(p, PartialExit) for p in mgmt.get("partials", [])]
            mgmt["trail"] = _take(mgmt.get("trail"), TrailSpec)
            mgmt = _take(mgmt, ManagementSpec)
        spec = cls(
            name=d.get("name", "Unnamed strategy"),
            version=str(d.get("version", "0.1")),
            description=d.get("description", ""),
            direction=d.get("direction", "long"),
            universe=_take(d.get("universe"), UniverseSpec),
            market_filter=_take(d.get("market_filter"), MarketFilter),
            sector_filter=_take(d.get("sector_filter"), SectorFilter),
            setup=_take(d.get("setup"), SetupSpec),
            entry=_take(d.get("entry"), EntrySpec),
            stop=_take(d.get("stop"), StopSpec),
            management=mgmt or ManagementSpec(),
            risk=_take(d.get("risk"), RiskSpec),
            costs=_take(d.get("costs"), CostSpec),
            provenance=d.get("provenance", []),
            notes=d.get("notes", ""),
        )
        spec.validate()
        return spec

    # -- validation ---------------------------------------------------------
    def validate(self) -> None:
        s, e, r = self.setup, self.entry, self.risk
        if s.cons_min_len < 2:
            raise SpecError("consolidation min length must be >= 2")
        if s.cons_max_len < s.cons_min_len:
            raise SpecError("consolidation max length must be >= min length")
        if not 0 <= e.breakout_min_close_position <= 1:
            raise SpecError("breakout close position must be between 0 and 1")
        if r.risk_pct <= 0 or r.risk_pct > 25:
            raise SpecError("risk per trade must be in (0, 25] percent")
        if r.max_positions < 1:
            raise SpecError("max positions must be >= 1")
        if self.direction not in ("long", "short"):
            raise SpecError("direction must be 'long' or 'short'")
        frac = sum(p.fraction for p in self.management.partials)
        if frac > 1.0 + 1e-9:
            raise SpecError(f"partial exits sum to {frac:.2f} of the position (>1)")
        for p in self.management.partials:
            if not 0 < p.fraction <= 1:
                raise SpecError("each partial fraction must be in (0, 1]")

    # -- helpers ------------------------------------------------------------
    def bumped(self, version: str, **changes: Any) -> "StrategySpec":
        """Immutable version bump -- previous versions are never overwritten."""
        d = self.to_dict()
        d.update(changes)
        d["version"] = version
        return StrategySpec.from_dict(d)

    def fingerprint(self) -> str:
        import hashlib
        payload = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]
