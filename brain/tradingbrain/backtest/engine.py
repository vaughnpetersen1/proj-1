"""Event-driven backtest engine.

DESIGN RULES (these are the reason to trust anything downstream)
---------------------------------------------------------------
1. **No look-ahead.** Every indicator array satisfies "element i uses only bars
   <= i" (asserted in ``tests/test_indicators.py``). A decision made on bar ``t``
   reads signals from bar ``t`` or earlier, and the default fill is the *next*
   bar's open.
2. **Gaps are real.** A stop does not fill at the stop price when the market
   gaps through it; it fills at the open. That is the default.
3. **Ambiguity resolves against the strategy.** When a bar's range contains both
   the stop and a profit target, the stop is assumed to have been hit first --
   daily bars cannot tell us the order, and the optimistic assumption is how
   backtests lie.
4. **Costs always apply.** Slippage and commission are charged on every fill,
   including partials.
5. **Survivorship is disclosed.** The engine cannot fix a universe that only
   contains today's listed names; it attaches the warning to every result.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import time
from typing import Any, Iterable

import numpy as np

from ..config import SETTINGS
from ..data.panel import Panel, build_panel
from ..data.providers.registry import HUB, DataHub
from ..data.quality import survivorship_note, validate
from ..data.types import PriceSeries
from ..indicators.core import sma
from ..strategy.evaluator import Evaluator, SymbolContext
from ..provenance import Claim, DataOrigin, EvidenceClass
from ..strategy.spec import StrategySpec


# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Fill:
    date: str
    price: float
    shares: float
    reason: str
    commission: float = 0.0
    slippage: float = 0.0


@dataclasses.dataclass
class Trade:
    symbol: str
    direction: str
    entry_date: str
    entry_price: float
    shares: float
    initial_stop: float
    risk_per_share: float
    risk_dollars: float
    sector: str | None = None
    setup_snapshot: dict[str, Any] = dataclasses.field(default_factory=dict)
    market_regime: str | None = None
    exits: list[Fill] = dataclasses.field(default_factory=list)
    exit_date: str | None = None
    avg_exit_price: float | None = None
    pnl: float = 0.0
    r_multiple: float = 0.0
    mae_r: float = 0.0
    mfe_r: float = 0.0
    bars_held: int = 0
    exit_reason: str | None = None
    commission: float = 0.0
    slippage_cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["exits"] = [dataclasses.asdict(f) for f in self.exits]
        return d


@dataclasses.dataclass
class _Position:
    symbol: str
    entry_date: dt.date
    entry_price: float
    shares: float
    stop: float
    initial_stop: float
    risk_per_share: float
    risk_dollars: float
    bars_held: int = 0
    partials_done: set[int] = dataclasses.field(default_factory=set)
    moved_to_breakeven: bool = False
    trail_exit_pending: bool = False
    pending_reason: str = ""
    mae: float = 0.0
    mfe: float = 0.0
    trade: Trade | None = None


@dataclasses.dataclass
class BacktestResult:
    spec: dict[str, Any]
    start: str
    end: str
    symbols: list[str]
    trades: list[Trade]
    equity_curve: list[dict[str, Any]]
    metrics: dict[str, Any]
    warnings: list[str]
    data_origin: str
    data_provider: str
    #: Everything needed to reproduce this run: dataset id and checksum, feed,
    #: adjustment methodology, universe, strategy version, costs, timestamp.
    provenance: dict[str, Any]
    runtime_seconds: float
    bars_processed: int
    signals_generated: int
    signals_rejected: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["trades"] = [t.to_dict() for t in self.trades]
        return d

    def claim(self) -> Claim:
        m = self.metrics
        return Claim(
            statement=(f"Simulated {m.get('n_trades', 0)} trades of "
                       f"'{self.spec.get('name')}' v{self.spec.get('version')} over "
                       f"{self.start}..{self.end}: win rate "
                       f"{m.get('win_rate_pct', float('nan')):.1f}%, expectancy "
                       f"{m.get('expectancy_r', float('nan')):.3f}R, profit factor "
                       f"{m.get('profit_factor', float('nan')):.2f}, max drawdown "
                       f"{m.get('max_drawdown_pct', float('nan')):.1f}%."),
            evidence=EvidenceClass.BACKTEST_RESULT,
            value=m,
            sample_size=int(m.get("n_trades", 0)),
            period=f"{self.start}..{self.end}",
            definition_of_success="a closed trade with positive net P/L after costs",
            data_source=self.data_provider,
            data_origin=DataOrigin(self.data_origin),
            methodology=(f"Dataset version {self.provenance.get('dataset_version_id')} "
                         f"(checksum {str(self.provenance.get('dataset_checksum'))[:12]}, "
                         f"{self.provenance.get('adjustment')}). "
                         "Event-driven daily simulation. Signals read bars <= t; default "
                         "fill is the next open. Stops fill at the open when the bar gaps "
                         "through them. When a bar contains both the stop and a target, the "
                         "stop is assumed first. Slippage "
                         f"{self.spec.get('costs', {}).get('slippage_bps')}bps and commission "
                         "charged on every fill."),
            caveats=list(self.warnings),
        )


# ---------------------------------------------------------------------------

class Backtester:
    def __init__(self, spec: StrategySpec, hub: DataHub = HUB,
                 start: dt.date | None = None, end: dt.date | None = None,
                 symbols: Iterable[str] | None = None,
                 panel: Panel | None = None,
                 progress: bool = False, lock_dataset: bool = True,
                 ensure_data: bool | None = None) -> None:
        spec.validate()
        self.lock_dataset = lock_dataset
        self.ensure_data = (hub.settings.allow_ondemand_backfill
                            if ensure_data is None else ensure_data)
        self.provenance: dict[str, Any] = {}
        self.spec = spec
        self.hub = hub
        self.start = start
        self.end = end
        self.progress = progress
        self.warnings: list[str] = []
        self.rejects: dict[str, int] = {}

        requested = list(symbols) if symbols is not None else (spec.universe.symbols or None)
        self.panel = panel if panel is not None else build_panel(requested, hub, start, end)
        self.symbols = [s for s in self.panel.symbols
                        if s.upper() not in {x.upper() for x in spec.universe.exclude}]
        self.data: dict[str, SymbolContext] = {}
        self.evaluator = Evaluator(spec)
        self.rs_percentile: np.ndarray | None = None
        self.sector_percentile: dict[tuple[str, dt.date], float] = {}

    # -- setup -------------------------------------------------------------
    def _prepare(self) -> None:
        if self.ensure_data:
            # Pre-flight: make the local store hold the window this run needs, so
            # nothing reaches for a vendor once the simulation is under way.
            report = self.hub.ensure(list(self.symbols), "1d", self.start, self.end)
            if report.get("rows_written"):
                self.warnings.append(
                    f"Pre-flight backfill wrote {report['rows_written']} bars from "
                    f"{report.get('provider')} before the run started.")
        origin_seen: set[str] = set()
        for sym in list(self.symbols):
            try:
                ser = self.hub.daily(sym, self.start, self.end)
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"{sym}: not loaded ({exc})")
                self.symbols.remove(sym)
                continue
            if len(ser) < 60:
                self.symbols.remove(sym)
                continue
            rep = validate(ser)
            for issue in rep.issues:
                if issue.severity == "error":
                    self.warnings.append(f"{sym}: data error -- {issue.code}: {issue.detail}")
            self.data[sym] = SymbolContext(ser, self.spec)
            origin_seen.add(ser.origin.value)

        self.data_origin = ("SYNTHETIC" if "SYNTHETIC" in origin_seen
                            else (origin_seen.pop() if len(origin_seen) == 1 else "MIXED"))
        self.data_provider = (self.data[self.symbols[0]].series.provider
                              if self.symbols else "none")
        if self.spec.setup.min_rs_percentile is not None:
            self.rs_percentile = self.panel.rank_percentile(
                self.panel.returns(self.spec.setup.rs_lookback))

        note = survivorship_note(f"{len(self.symbols)} symbols from the active data path")
        self.warnings.append(note.detail)
        self._lock_dataset()
        if self.data_origin == "SYNTHETIC":
            self.warnings.append(
                "Data origin is SYNTHETIC. These results exercise the engine; they are not "
                "evidence about real markets.")

        # market filter arrays, aligned by date
        self.market_ok: dict[dt.date, bool] = {}
        mf = self.spec.market_filter
        if mf.enabled:
            per_symbol: list[dict[dt.date, bool]] = []
            for sym in filter(None, (mf.symbol, mf.secondary_symbol)):
                try:
                    ser = self.hub.daily(sym)
                except Exception as exc:  # noqa: BLE001
                    self.warnings.append(
                        f"Market filter symbol {sym} unavailable ({exc}); filter disabled "
                        "for this run. Results are NOT comparable to a filtered run.")
                    per_symbol = []
                    break
                f, s = sma(ser.close, mf.fast), sma(ser.close, mf.slow)
                ok = (f > s) if mf.require_fast_above_slow else np.ones(len(ser), bool)
                ok &= np.isfinite(f) & np.isfinite(s)
                if mf.require_close_above_slow:
                    ok &= ser.close > s
                if mf.max_drawdown_from_high_pct is not None:
                    from ..indicators.core import rolling_max
                    hi = rolling_max(ser.close, 252)
                    with np.errstate(invalid="ignore"):
                        dd = (ser.close / hi - 1.0) * 100.0
                    ok &= dd >= -abs(mf.max_drawdown_from_high_pct)
                per_symbol.append({t.date(): bool(o) for t, o in zip(ser.ts, ok)})
            if per_symbol:
                dates = set().union(*[set(d) for d in per_symbol])
                for d in dates:
                    vals = [d_map.get(d) for d_map in per_symbol]
                    vals = [v for v in vals if v is not None]
                    if not vals:
                        continue
                    self.market_ok[d] = all(vals) if mf.mode == "both" else any(vals)

    def _lock_dataset(self) -> None:
        """Freeze the exact bars this run will use, before it starts.

        Without this, a result cannot be reproduced: the store keeps growing, a
        re-adjustment changes prices, and a provider switch changes them again.
        The dataset id and checksum pin the run to the bytes it actually saw.
        """
        from ..marketstore.store import MARKET
        costs = {"slippage_bps": self.spec.costs.slippage_bps,
                 "commission_per_share": self.spec.costs.commission_per_share,
                 "commission_min": self.spec.costs.commission_min,
                 "gap_fills_at_open": self.spec.costs.gap_fills_at_open}
        base = {
            "strategy": f"{self.spec.name} v{self.spec.version}",
            "strategy_fingerprint": self.spec.fingerprint(),
            "timeframe": "1d",
            "universe_size": len(self.symbols),
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "provider": self.data_provider,
            "data_origin": self.data_origin,
            "costs": costs,
            "locked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        if not self.lock_dataset or not self.symbols:
            base["dataset_version_id"] = None
            base["dataset_note"] = ("Dataset was not locked for this run, so the exact "
                                    "bars cannot be pinned. Results are not reproducible "
                                    "against a changing store.")
            self.provenance = base
            self.warnings.append(base["dataset_note"])
            return
        try:
            adjusted = all(self.data[s].series.adjusted for s in self.symbols
                           if s in self.data)
            feeds = {w.split("=", 1)[1] for s in self.symbols if s in self.data
                     for w in self.data[s].series.warnings if w.startswith("feed=")}
            ds = MARKET.lock_dataset(
                symbols=self.symbols, timeframe="1d", start=self.start, end=self.end,
                provider=self.data_provider, feed=(next(iter(feeds)) if len(feeds) == 1
                                                   else None),
                origin=self.data_origin,
                adjustment="split_and_dividend" if adjusted else "none_or_unknown",
                label=f"{self.spec.name} v{self.spec.version}")
            base.update(dataset_version_id=ds["id"], dataset_checksum=ds["checksum"],
                        dataset_rows=ds["row_count"], dataset_reused=ds["reused"],
                        feed=ds["feed"], adjustment=ds["adjustment"])
        except Exception as exc:                              # noqa: BLE001
            base["dataset_version_id"] = None
            base["dataset_error"] = f"{type(exc).__name__}: {exc}"
            self.warnings.append(
                "Dataset could not be locked (" + base["dataset_error"] + "); this run "
                "is not reproducible against a changing store.")
        self.provenance = base

    # -- helpers -----------------------------------------------------------
    def _market_allows(self, day: dt.date) -> bool:
        if not self.spec.market_filter.enabled:
            return True
        if day in self.market_ok:
            return self.market_ok[day]
        # last known state at or before `day` -- never a future one
        prior = [d for d in self.market_ok if d <= day]
        return self.market_ok[max(prior)] if prior else False

    def _reject(self, key: str) -> None:
        self.rejects[key] = self.rejects.get(key, 0) + 1

    def _slip(self, price: float, side: str) -> float:
        bps = self.spec.costs.slippage_bps / 10_000.0
        return price * (1 + bps) if side == "buy" else price * (1 - bps)

    def _commission(self, shares: float) -> float:
        c = self.spec.costs
        return max(c.commission_min, shares * c.commission_per_share) if shares else 0.0

    # -- signal ------------------------------------------------------------
    def _qualifies(self, sym: str, i: int, panel_row: int) -> tuple[bool, dict[str, Any]]:
        """Delegates to the shared Evaluator so the backtester, the scanner, the
        analyzer and the analog search all mean the same thing by 'a setup'."""
        ctx = self.data[sym]
        if not ctx.breakouts[i]:
            return False, {}
        self._sig_count = getattr(self, "_sig_count", 0) + 1
        rs = None
        if self.rs_percentile is not None and panel_row >= 0:
            try:
                val = self.rs_percentile[panel_row, self.panel.col(sym)]
                rs = float(val) if np.isfinite(val) else None
            except ValueError:
                rs = None
        sector_pct = self.sector_percentile.get((sym, ctx.series.ts[i].date())) \
            if self.sector_percentile else None
        v = self.evaluator.evaluate(ctx, i, rs_percentile=rs, sector_percentile=sector_pct)
        if not v.qualifies:
            self._reject(v.primary_failure)
            return False, v.snapshot
        return True, v.snapshot

    def _stop_for(self, sym: str, i: int, entry: float) -> float:
        d = self.data[sym]
        st = self.spec.stop
        ser = d.series
        if st.type == "low_of_day":
            stop = float(ser.low[i])
        elif st.type == "low_of_base":
            base = d.cons.at(i - 1)
            stop = float(base.low) if base else float(ser.low[i])
        elif st.type == "pct":
            stop = entry * (1 - st.value / 100.0)
        elif st.type == "atr":
            a = d.atr14[i]
            stop = entry - st.value * float(a) if np.isfinite(a) else entry * 0.95
        elif st.type == "adr":
            a = d.adr[i]
            stop = entry * (1 - st.value * float(a) / 100.0) if np.isfinite(a) else entry * 0.95
        else:
            stop = float(ser.low[i])
        return min(stop, entry * 0.999)

    # -- main loop ---------------------------------------------------------
    def run(self) -> BacktestResult:
        t0 = time.time()
        self._prepare()
        spec = self.spec
        equity = float(spec.risk.account_equity)
        cash = equity
        positions: dict[str, _Position] = {}
        pending: list[tuple[str, int, dict[str, Any]]] = []   # (symbol, signal_idx, snapshot)
        trades: list[Trade] = []
        curve: list[dict[str, Any]] = []
        bars = 0

        dates = self.panel.dates
        if self.start:
            dates = [d for d in dates if d >= self.start]
        if self.end:
            dates = [d for d in dates if d <= self.end]

        for panel_row, day in enumerate(dates):
            row = self.panel.row(day)

            # ---------- 1. fills for orders generated on prior bars -------
            still_pending: list[tuple[str, int, dict[str, Any]]] = []
            for sym, sig_i, snap in pending:
                d = self.data[sym]
                i = d.date_index.get(day)
                if i is None:
                    continue                     # symbol did not trade; order lapses
                if sym in positions and not spec.risk.allow_pyramiding:
                    self._reject("already in position")
                    continue
                if len(positions) >= spec.risk.max_positions:
                    self._reject("max positions")
                    continue
                fill_price = float(d.series.open[i])
                if spec.entry.fill == "stop_at_level":
                    level = snap["breakout_level"]
                    if d.series.high[i] < level:
                        continue
                    fill_price = max(level, float(d.series.open[i]))
                entry = self._slip(fill_price, "buy")
                stop = self._stop_for(sym, sig_i, entry)
                rps = entry - stop
                if rps <= 0:
                    self._reject("non-positive risk per share")
                    continue
                # Kullamagi's ADR cap on stop width
                cap = spec.stop.max_stop_adr_multiple
                adr = float(d.adr[sig_i]) if np.isfinite(d.adr[sig_i]) else None
                if cap is not None and adr:
                    if (rps / entry * 100.0) > cap * adr:
                        self._reject("stop wider than ADR cap")
                        continue
                if spec.stop.max_stop_pct is not None and rps / entry * 100.0 > spec.stop.max_stop_pct:
                    self._reject("stop wider than max %")
                    continue

                risk_dollars = equity * spec.risk.risk_pct / 100.0
                shares = math.floor(risk_dollars / rps)
                max_by_pos = math.floor(equity * spec.risk.max_position_pct / 100.0 / entry)
                exposure = 0.0
                for p_ in positions.values():
                    pd_ = self.data[p_.symbol]
                    k_ = pd_.date_index.get(day)
                    # A symbol with no bar today is marked at its entry price, never at
                    # its last-ever bar: index -1 would read the future.
                    exposure += p_.shares * (float(pd_.series.close[k_]) if k_ is not None
                                             else p_.entry_price)
                room = equity * spec.risk.max_portfolio_exposure_pct / 100.0 - exposure
                max_by_exposure = math.floor(max(room, 0) / entry)
                max_by_cash = math.floor(max(cash, 0) / entry)
                shares = max(0, min(shares, max_by_pos, max_by_exposure, max_by_cash))
                if shares < 1:
                    self._reject("size rounds to zero")
                    continue

                comm = self._commission(shares)
                cash -= shares * entry + comm
                tr = Trade(symbol=sym, direction=spec.direction, entry_date=day.isoformat(),
                           entry_price=entry, shares=shares, initial_stop=stop,
                           risk_per_share=rps, risk_dollars=rps * shares,
                           sector=self.hub.sector_map().get(sym),
                           setup_snapshot=snap,
                           market_regime="filter_pass" if self._market_allows(day) else "filter_fail",
                           commission=comm,
                           slippage_cost=abs(entry - fill_price) * shares)
                positions[sym] = _Position(symbol=sym, entry_date=day, entry_price=entry,
                                           shares=shares, stop=stop, initial_stop=stop,
                                           risk_per_share=rps, risk_dollars=rps * shares,
                                           trade=tr)
            pending = still_pending

            # ---------- 2. manage open positions on today's bar ------------
            for sym in list(positions):
                pos = positions[sym]
                d = self.data[sym]
                i = d.date_index.get(day)
                if i is None:
                    continue
                bars += 1
                ser = d.series
                o, h, l, c = (float(ser.open[i]), float(ser.high[i]),
                              float(ser.low[i]), float(ser.close[i]))
                if day == pos.entry_date:
                    pos.bars_held = 0
                else:
                    pos.bars_held += 1

                # excursions in R
                pos.mfe = max(pos.mfe, (h - pos.entry_price) / pos.risk_per_share)
                pos.mae = min(pos.mae, (l - pos.entry_price) / pos.risk_per_share)

                # 2a. a trail/time exit signalled on the previous close executes at this open
                if pos.trail_exit_pending:
                    cash += self._close_position(pos, day, self._slip(o, "sell"),
                                                 pos.pending_reason, trades)
                    del positions[sym]
                    continue

                # 2b. gap through the stop -> fill at the open (never at the stop price)
                if spec.costs.gap_fills_at_open and o <= pos.stop:
                    cash += self._close_position(pos, day, self._slip(o, "sell"),
                                                 "gap through stop", trades)
                    del positions[sym]
                    continue

                # 2c. stop inside the bar. Assumed to precede any target in the same bar.
                if l <= pos.stop:
                    cash += self._close_position(pos, day, self._slip(pos.stop, "sell"),
                                                 "stop", trades)
                    del positions[sym]
                    continue

                # 2d. hard target
                if spec.management.target_r is not None:
                    tgt = pos.entry_price + spec.management.target_r * pos.risk_per_share
                    if h >= tgt:
                        cash += self._close_position(pos, day, self._slip(tgt, "sell"),
                                                     f"target {spec.management.target_r}R",
                                                     trades)
                        del positions[sym]
                        continue

                # 2e. partial exits
                for k, part in enumerate(spec.management.partials):
                    if k in pos.partials_done:
                        continue
                    hit_r = part.at_r > 0 and h >= pos.entry_price + part.at_r * pos.risk_per_share
                    hit_days = part.after_days is not None and pos.bars_held >= part.after_days
                    if not (hit_r or hit_days):
                        continue
                    price = (pos.entry_price + part.at_r * pos.risk_per_share) if hit_r else c
                    price = self._slip(min(max(price, l), h), "sell")
                    qty = math.floor(pos.shares * part.fraction)
                    if qty < 1 or qty >= pos.shares:
                        qty = min(max(1, qty), max(0, pos.shares - 1))
                    if qty < 1:
                        pos.partials_done.add(k)
                        continue
                    comm = self._commission(qty)
                    cash += qty * price - comm
                    pos.shares -= qty
                    pos.trade.exits.append(Fill(day.isoformat(), price, qty,
                                                f"partial {part.fraction:.0%} "
                                                f"({'+%.1fR' % part.at_r if hit_r else '%dd' % part.after_days})",
                                                comm))
                    pos.trade.commission += comm
                    pos.partials_done.add(k)

                # 2f. breakeven
                m = spec.management
                if not pos.moved_to_breakeven:
                    by_r = (m.breakeven_after_r is not None
                            and h >= pos.entry_price + m.breakeven_after_r * pos.risk_per_share)
                    by_d = (m.breakeven_after_days is not None
                            and pos.bars_held >= m.breakeven_after_days)
                    if by_r or by_d:
                        pos.stop = max(pos.stop, pos.entry_price)
                        pos.moved_to_breakeven = True

                # 2g. trailing stop / time stop, evaluated on the close, acted next open
                trail = m.trail
                if trail.type in ("sma", "ema") and np.isfinite(d.trail_ma[i]):
                    active = (trail.activate_after_r is None
                              or pos.mfe >= trail.activate_after_r)
                    basis = c if trail.basis == "close" else l
                    if active and basis < d.trail_ma[i]:
                        pos.trail_exit_pending = True
                        pos.pending_reason = f"closed below {trail.length} {trail.type.upper()}"
                elif trail.type in ("atr", "chandelier"):
                    a = d.atr14[i]
                    if np.isfinite(a):
                        ref = (max(ser.high[max(0, i - 21):i + 1]) if trail.type == "chandelier"
                               else c)
                        pos.stop = max(pos.stop, float(ref - trail.multiple * a))

                if m.time_stop_days is not None and pos.bars_held >= m.time_stop_days:
                    r_now = (c - pos.entry_price) / pos.risk_per_share
                    if r_now < m.time_stop_min_r:
                        pos.trail_exit_pending = True
                        pos.pending_reason = (f"time stop: below {m.time_stop_min_r}R after "
                                              f"{m.time_stop_days} bars")
                if m.max_hold_days is not None and pos.bars_held >= m.max_hold_days:
                    pos.trail_exit_pending = True
                    pos.pending_reason = f"max hold {m.max_hold_days} bars"

            # ---------- 3. generate tomorrow's orders from today's close ---
            if self._market_allows(day):
                cands: list[tuple[float, str, int, dict]] = []
                for sym in self.symbols:
                    d = self.data.get(sym)
                    if d is None:
                        continue
                    i = d.date_index.get(day)
                    if i is None or i < 60:
                        continue
                    if sym in positions and not spec.risk.allow_pyramiding:
                        continue
                    ok, snap = self._qualifies(sym, i, row)
                    if ok:
                        cands.append((snap.get("base_quality", 0.0), sym, i, snap))
                # deterministic priority when more signals than slots: best base first
                cands.sort(key=lambda x: (-x[0], x[1]))
                free = max(0, spec.risk.max_positions - len(positions))
                for _, sym, i, snap in cands[:free]:
                    pending.append((sym, i, snap))
                if len(cands) > free:
                    self._reject("no free slot")
            else:
                self._reject("market filter")

            # ---------- 4. mark to market ---------------------------------
            mtm = 0.0
            for pos in positions.values():
                d = self.data[pos.symbol]
                i = d.date_index.get(day)
                px = float(d.series.close[i]) if i is not None else pos.entry_price
                mtm += pos.shares * px
            equity = cash + mtm
            curve.append({"date": day.isoformat(), "equity": round(equity, 2),
                          "cash": round(cash, 2), "positions": len(positions),
                          "exposure_pct": round(100.0 * mtm / equity, 2) if equity else 0.0})

        # ---------- 5. close anything still open at the final bar ----------
        if dates:
            last = dates[-1]
            for sym in list(positions):
                pos = positions[sym]
                d = self.data[sym]
                i = d.date_index.get(last)
                px = float(d.series.close[i]) if i is not None else pos.entry_price
                cash += self._close_position(pos, last, self._slip(px, "sell"),
                                             "open at end of test", trades)
                del positions[sym]
            if curve:
                curve[-1]["equity"] = round(cash, 2)

        from .metrics import compute_metrics
        metrics = compute_metrics(trades, curve, spec.risk.account_equity)
        runtime = time.time() - t0
        return BacktestResult(
            spec=spec.to_dict(),
            start=dates[0].isoformat() if dates else "",
            end=dates[-1].isoformat() if dates else "",
            symbols=self.symbols, trades=trades, equity_curve=curve, metrics=metrics,
            warnings=self.warnings, data_origin=self.data_origin,
            data_provider=self.data_provider, provenance=self.provenance,
            runtime_seconds=round(runtime, 2),
            bars_processed=bars, signals_generated=getattr(self, "_sig_count", 0),
            signals_rejected=dict(sorted(self.rejects.items(), key=lambda kv: -kv[1])))

    # -- exit --------------------------------------------------------------
    def _close_position(self, pos: _Position, day: dt.date, price: float,
                        reason: str, trades: list[Trade]) -> float:
        qty = pos.shares
        comm = self._commission(qty)
        proceeds = qty * price - comm
        tr = pos.trade
        tr.exits.append(Fill(day.isoformat(), price, qty, reason, comm))
        tr.commission += comm
        tr.exit_date = day.isoformat()
        tr.exit_reason = reason
        total_qty = sum(f.shares for f in tr.exits)
        gross = sum(f.price * f.shares for f in tr.exits)
        tr.avg_exit_price = gross / total_qty if total_qty else price
        tr.pnl = gross - tr.entry_price * total_qty - tr.commission
        tr.r_multiple = tr.pnl / tr.risk_dollars if tr.risk_dollars else 0.0
        tr.mae_r = round(pos.mae, 3)
        tr.mfe_r = round(pos.mfe, 3)
        tr.bars_held = pos.bars_held
        trades.append(tr)
        return proceeds


def run_backtest(spec: StrategySpec, hub: DataHub = HUB, start: dt.date | None = None,
                 end: dt.date | None = None, symbols: Iterable[str] | None = None,
                 panel: Panel | None = None) -> BacktestResult:
    return Backtester(spec, hub, start, end, symbols, panel).run()
