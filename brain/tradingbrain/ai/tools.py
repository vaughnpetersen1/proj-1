"""The tool layer the AI is allowed to use.

ARCHITECTURAL RULE: the language model never produces a number. It chooses a
tool and arguments; the tool computes against real engines and real data; the
model's job is to relay and frame what came back. Every tool returns structured
JSON including provenance, so a fabricated figure has nowhere to hide -- if a
number is not in a tool result, it is not in the answer.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import inspect
from typing import Any, Callable

import numpy as np

from ..config import SETTINGS
from ..data.panel import build_panel
from ..data.providers.registry import HUB
from ..data.quality import validate
from ..data.types import Timeframe
from ..db.store import STORE
from ..indicators.core import (adr_pct, atr, dollar_volume, ema, relative_volume, rsi,
                               sma, slope_r2)
from ..indicators.structure import (extension_metrics, find_prior_move, opening_range,
                                    scan_consolidations)
from ..provenance import EVIDENCE_DESCRIPTIONS

_PANEL_CACHE: dict[str, Any] = {}


def _panel():
    if "p" not in _PANEL_CACHE:
        _PANEL_CACHE["p"] = build_panel()
    return _PANEL_CACHE["p"]


def _date(v: Any) -> dt.date | None:
    if not v:
        return None
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def _f(x) -> float | None:
    try:
        v = float(x)
        return round(v, 6) if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# market data
# ---------------------------------------------------------------------------

def get_market_data(symbol: str, bars: int = 60) -> dict[str, Any]:
    """Recent daily bars and the latest quote-like snapshot for one symbol."""
    s = HUB.daily(symbol)
    if len(s) == 0:
        return {"error": f"no data for {symbol}"}
    tail = s.slice(max(0, len(s) - bars), len(s))
    last = len(s) - 1
    return {
        "symbol": s.symbol, "as_of": s.ts[last].date().isoformat(),
        "close": _f(s.close[last]), "open": _f(s.open[last]),
        "high": _f(s.high[last]), "low": _f(s.low[last]), "volume": _f(s.volume[last]),
        "change_pct": _f((s.close[last] / s.close[last - 1] - 1) * 100) if last else None,
        "bars": tail.to_records(),
        "provider": s.provider, "data_origin": s.origin.value,
        "note": HUB.data_note(),
    }


def get_historical_data(symbol: str, start: str | None = None, end: str | None = None,
                        max_bars: int = 5000) -> dict[str, Any]:
    s = HUB.daily(symbol, _date(start), _date(end))
    if len(s) > max_bars:
        s = s.slice(len(s) - max_bars, len(s))
    return {"symbol": s.symbol, "timeframe": "1d", "bars": s.to_records(),
            **s.describe(), "note": HUB.data_note()}


def get_intraday_data(symbol: str, timeframe: str = "5m", start: str | None = None,
                      end: str | None = None) -> dict[str, Any]:
    try:
        tf = Timeframe(timeframe)
    except ValueError:
        return {"error": f"unknown timeframe {timeframe!r}",
                "valid": [t.value for t in Timeframe]}
    try:
        s = HUB.intraday(symbol, tf, _date(start), _date(end))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc),
                "note": "No intraday provider is reachable in this environment."}
    return {"symbol": s.symbol, "timeframe": tf.value, "bars": s.to_records()[-500:],
            **s.describe()}


def get_options_chain(symbol: str, expiration: str | None = None) -> dict[str, Any]:
    from ..options.chain import get_chain
    return get_chain(symbol, expiration)


def get_news(symbol: str, since: str | None = None) -> dict[str, Any]:
    """No news provider is configured. Says so instead of inventing headlines."""
    return {
        "available": False, "symbol": symbol.upper(),
        "reason": ("No news provider is configured or reachable. Set NEWS_API_KEY or "
                   "FINNHUB_API_KEY and make the host reachable."),
        "impact": ("Catalyst-driven setups (episodic pivots) cannot be identified without "
                   "this. Any 'episodic pivot' statistics in this build describe large "
                   "gap-ups, a strict superset of the real setup."),
        "articles": [],
    }


def calculate_indicators(symbol: str, indicators: list[str] | None = None,
                         as_of: str | None = None) -> dict[str, Any]:
    s = HUB.daily(symbol, end=_date(as_of))
    if len(s) < 30:
        return {"error": f"only {len(s)} bars for {symbol}"}
    i = len(s) - 1
    c, h, l, v = s.close, s.high, s.low, s.volume
    all_ind = {
        "close": _f(c[i]),
        "sma10": _f(sma(c, 10)[i]), "sma20": _f(sma(c, 20)[i]),
        "sma50": _f(sma(c, 50)[i]), "sma200": _f(sma(c, 200)[i]),
        "ema21": _f(ema(c, 21)[i]),
        "atr14": _f(atr(h, l, c, 14)[i]),
        "adr20_pct": _f(adr_pct(h, l, 20)[i]),
        "rsi14": _f(rsi(c, 14)[i]),
        "rvol50": _f(relative_volume(v, 50)[i]),
        "dollar_volume_20d": _f(dollar_volume(c, v, 20)[i]),
        "r2_20": _f(slope_r2(c, 20)[i]),
        "pct_above_sma20": _f((c[i] / sma(c, 20)[i] - 1) * 100),
    }
    if indicators:
        all_ind = {k: v2 for k, v2 in all_ind.items() if k in set(indicators) | {"close"}}
    return {"symbol": s.symbol, "as_of": s.ts[i].date().isoformat(), "indicators": all_ind,
            "data_origin": s.origin.value, "provider": s.provider}


def calculate_relative_strength(symbol: str, lookback: int = 63,
                                as_of: str | None = None) -> dict[str, Any]:
    p = _panel()
    row = p.row(_date(as_of)) if as_of else len(p.dates) - 1
    if symbol.upper() not in p.symbols:
        return {"error": f"{symbol} is not in the scanned universe"}
    j = p.col(symbol)
    rets = p.returns(lookback)
    pct = p.rank_percentile(rets)
    return {"symbol": symbol.upper(), "as_of": p.dates[row].isoformat(),
            "lookback_bars": lookback,
            "return_pct": _f(rets[row, j]),
            "percentile_vs_universe": _f(pct[row, j]),
            "universe_size": len(p.symbols),
            "note": "Percentile is against the symbols this data path can serve, which is "
                    "not the full market."}


def detect_consolidation(symbol: str, as_of: str | None = None) -> dict[str, Any]:
    from ..strategy.library import get_strategy
    from ..strategy.evaluator import consolidation_params
    spec = get_strategy("sar_v1_1_adr_stop")
    s = HUB.daily(symbol, end=_date(as_of))
    if len(s) < 60:
        return {"error": f"only {len(s)} bars for {symbol}"}
    scan = scan_consolidations(s.high, s.low, s.close, s.volume,
                               consolidation_params(spec.setup), sma(s.close, 20))
    base = scan.at(len(s) - 1)
    return {"symbol": s.symbol, "as_of": s.ts[-1].date().isoformat(),
            "consolidation": base.to_dict() if base else None,
            "found": base is not None,
            "definition": dataclasses.asdict(consolidation_params(spec.setup))}


def detect_breakout(symbol: str, as_of: str | None = None,
                    strategy_key: str = "sar_v1_1_adr_stop") -> dict[str, Any]:
    from ..strategy.evaluator import Evaluator, SymbolContext
    from ..strategy.library import get_strategy
    spec = get_strategy(strategy_key)
    s = HUB.daily(symbol, end=_date(as_of))
    if len(s) < 60:
        return {"error": f"only {len(s)} bars for {symbol}"}
    ctx = SymbolContext(s, spec)
    i = len(s) - 1
    v = Evaluator(spec).evaluate(ctx, i, require_breakout=bool(ctx.breakouts[i]),
                                 collect_all_reasons=True)
    return {"symbol": s.symbol, "as_of": s.ts[i].date().isoformat(),
            "breakout_triggered": bool(ctx.breakouts[i]),
            "qualifies_under_strategy": v.qualifies,
            "failed_checks": v.reasons_failed,
            "measurements": v.snapshot, "strategy": f"{spec.name} v{spec.version}"}


def detect_market_regime(as_of: str | None = None, with_breadth: bool = True) -> dict[str, Any]:
    from ..regime.classifier import classify_market
    r = classify_market(_date(as_of), HUB, with_breadth=with_breadth)
    return {**r.to_dict(), "claim": r.claim().to_dict()}


def rank_sectors_tool(as_of: str | None = None, top: int = 12) -> dict[str, Any]:
    from ..sectors.ranking import rank_sectors
    r = rank_sectors(_date(as_of), HUB, _panel())
    r["ranks"] = r["ranks"][:top]
    return r


def rank_themes_tool(as_of: str | None = None, top: int = 12) -> dict[str, Any]:
    from ..sectors.ranking import rank_themes
    r = rank_themes(_date(as_of), HUB, _panel())
    r["ranks"] = r["ranks"][:top]
    return r


def rank_stocks(as_of: str | None = None, limit: int = 20,
                strategy_key: str = "sar_v1_1_adr_stop",
                include_pre_breakout: bool = True) -> dict[str, Any]:
    from ..scanner.scan import ScanConfig, run_scan
    return run_scan(ScanConfig(strategy_key=strategy_key, as_of=_date(as_of),
                               limit=limit, include_pre_breakout=include_pre_breakout),
                    HUB, _panel())


def find_historical_setups_tool(strategy_key: str = "sar_v1_0", start: str | None = None,
                                end: str | None = None, target_r: float = 3.0,
                                exclude_symbol: str | None = None,
                                max_events: int = 8000) -> dict[str, Any]:
    from ..stats.analogs import AnalogQuery, analog_statistics, find_historical_setups
    from ..strategy.library import get_strategy
    q = AnalogQuery(spec=get_strategy(strategy_key), start=_date(start), end=_date(end),
                    target_r=target_r, exclude_symbol=exclude_symbol, max_events=max_events)
    events, meta = find_historical_setups(q, HUB, _panel())
    return analog_statistics(events, q, meta)


def calculate_statistics(values: list[float], name: str = "values") -> dict[str, Any]:
    from ..stats.core import bootstrap_ci, describe
    d = describe(values, name)
    if d.get("n", 0) >= 3:
        ci = bootstrap_ci(values, np.mean, n_boot=3000, name=f"mean {name}")
        d["mean_ci_95"] = [ci.ci_low, ci.ci_high]
        d["sample_verdict"] = ci.verdict
    return d


def run_backtest_tool(strategy_key: str = "sar_v1_0", start: str | None = None,
                      end: str | None = None, save: bool = True,
                      max_symbols: int | None = None) -> dict[str, Any]:
    from ..backtest.engine import Backtester
    from ..strategy.library import get_strategy
    spec = get_strategy(strategy_key)
    panel = build_panel(None, HUB, _date(start), _date(end), max_symbols=max_symbols) \
        if max_symbols else _panel()
    r = Backtester(spec, HUB, _date(start), _date(end), panel=panel).run()
    bid = None
    if save:
        bid = STORE.save_backtest(
            label=f"{spec.name} v{spec.version}", spec=spec.to_dict(), metrics=r.metrics,
            trades=[t.to_dict() for t in r.trades][:2000], equity_curve=r.equity_curve,
            data_origin=r.data_origin, data_provider=r.data_provider,
            warnings=r.warnings, dataset_version_id=r.provenance.get("dataset_version_id"),
            provenance=r.provenance, runtime_seconds=r.runtime_seconds)
    return {"backtest_id": bid, "provenance": r.provenance,
            "strategy": f"{spec.name} v{spec.version}",
            "start": r.start, "end": r.end, "metrics": r.metrics,
            "signals_generated": r.signals_generated,
            "signals_rejected": r.signals_rejected,
            "warnings": r.warnings, "data_origin": r.data_origin,
            "data_provider": r.data_provider, "n_symbols": len(r.symbols),
            "runtime_seconds": r.runtime_seconds, "claim": r.claim().to_dict()}


def run_walk_forward_test(strategy_key: str = "sar_v1_0",
                          parameter: str = "setup.prior_move_min_pct",
                          values: list[float] | None = None,
                          start: str = "2012-01-01", end: str | None = None,
                          train_years: float = 4.0, test_years: float = 1.0,
                          objective: str = "expectancy_r",
                          max_symbols: int | None = 40) -> dict[str, Any]:
    from ..backtest.walkforward import run_walk_forward
    from ..strategy.library import get_strategy
    grid = {parameter: values or [20, 30, 40]}
    panel = build_panel(None, HUB, _date(start), _date(end), max_symbols=max_symbols)
    end_d = _date(end) or panel.dates[-1]
    return run_walk_forward(get_strategy(strategy_key), grid, _date(start), end_d, HUB,
                            train_years, test_years, objective, panel=panel)


def run_monte_carlo_tool(backtest_id: int | None = None, r_multiples: list[float] | None = None,
                         simulations: int = 5000, risk_pct: float = 1.0,
                         method: str = "bootstrap") -> dict[str, Any]:
    from ..backtest.montecarlo import MonteCarloConfig, run_monte_carlo
    origin = provider = "UNKNOWN"
    if r_multiples is None:
        if backtest_id is None:
            bts = STORE.backtests(limit=1)
            backtest_id = bts[0]["id"] if bts else None
        if backtest_id is None:
            return {"ok": False, "reason": "no backtest available; run one first"}
        bt = STORE.backtest(backtest_id)
        if not bt:
            return {"ok": False, "reason": f"backtest {backtest_id} not found"}
        r_multiples = [t.get("r_multiple", 0.0) for t in (bt.get("trades") or [])]
        origin, provider = bt.get("data_origin") or "UNKNOWN", bt.get("data_provider") or "unknown"
    cfg = MonteCarloConfig(n_simulations=simulations, risk_pct=risk_pct, method=method)
    return run_monte_carlo(r_multiples, cfg, origin, provider)


def run_sensitivity_analysis(strategy_key: str = "sar_v1_0",
                             parameter: str = "setup.prior_move_min_pct",
                             values: list[float] | None = None,
                             start: str | None = "2015-01-01", end: str | None = None,
                             max_symbols: int | None = 40) -> dict[str, Any]:
    from ..backtest.sensitivity import run_sensitivity
    from ..strategy.library import get_strategy
    panel = build_panel(None, HUB, _date(start), _date(end), max_symbols=max_symbols)
    return run_sensitivity(get_strategy(strategy_key), {parameter: values or [20, 25, 30, 35, 40]},
                           _date(start), _date(end), HUB, panel=panel)


def calculate_position_size(account_equity: float, risk_pct: float, entry: float,
                            stop: float, target: float | None = None,
                            direction: str = "long") -> dict[str, Any]:
    from ..risk.sizing import position_size
    return position_size(account_equity, risk_pct, entry, stop, target, direction).to_dict()


def analyze_options(symbol: str, contracts: list[dict[str, Any]] | None = None,
                    move_pct: float = 10.0, hold_days: int = 10) -> dict[str, Any]:
    from ..options.analysis import compare_contracts
    from ..options.chain import expirations, get_chain
    if not contracts:
        ch = get_chain(symbol)
        spot, exps = ch["underlying"], sorted({c["expiration"] for c in ch["contracts"]})
        pick = exps[min(3, len(exps) - 1)]
        contracts = [{"strike": spot, "expiration": pick, "type": "call"},
                     {"strike": spot * 1.05, "expiration": pick, "type": "call"},
                     {"strike": spot * 1.10, "expiration": pick, "type": "call"}]
    return compare_contracts(symbol, contracts, HUB, move_pct=move_pct, hold_days=hold_days)


def calculate_risk_reward(entry: float, stop: float, target: float,
                          direction: str = "long") -> dict[str, Any]:
    from ..risk.sizing import risk_reward
    return risk_reward(entry, stop, target, direction)


def get_trade_history(limit: int = 200, ticker: str | None = None) -> dict[str, Any]:
    trades = STORE.trades(limit=limit, ticker=ticker.upper() if ticker else None)
    from ..journal.analytics import journal_summary
    return {"summary": journal_summary(STORE), "trades": trades}


def analyze_trading_performance_tool() -> dict[str, Any]:
    from ..journal.analytics import analyze_trading_performance
    return analyze_trading_performance(STORE)


def search_strategy_knowledge(query: str | None = None,
                              category: str | None = None) -> dict[str, Any]:
    from ..research.knowledge import knowledge_stats, search_knowledge
    return {"results": search_knowledge(query, category, STORE),
            "stats": knowledge_stats(STORE)}


def search_source_material(concept: str) -> dict[str, Any]:
    from ..research.knowledge import why_rule
    return why_rule(concept, STORE)


def create_hypothesis_tool(question: str, statement: str = "") -> dict[str, Any]:
    from ..research.hypothesis import create_hypothesis
    return create_hypothesis(question, statement, store=STORE)


def run_hypothesis_test_tool(question: str | None = None, hypothesis_id: int | None = None,
                            kind: str | None = None, feature: str | None = None,
                            concept: str | None = None, horizon: int = 10,
                            start: str | None = "2012-01-01",
                            max_symbols: int | None = None) -> dict[str, Any]:
    from ..research.hypothesis import HypothesisSpec, infer_spec, run_hypothesis_test
    if hypothesis_id:
        row = STORE.one("SELECT * FROM hypotheses WHERE id=?", (hypothesis_id,))
        if not row:
            return {"error": f"hypothesis {hypothesis_id} not found"}
        import json
        spec = HypothesisSpec(**json.loads(row["spec"]))
        question = question or row["question"]
    elif kind:
        spec = HypothesisSpec(kind=kind, question=question or "", feature=feature,
                              concept=concept, horizon=horizon, start=start,
                              max_symbols=max_symbols)
    elif question:
        spec = infer_spec(question)
        spec.question = question
        spec.start = start
        spec.horizon = horizon
        spec.max_symbols = max_symbols
    else:
        return {"error": "supply a question, a hypothesis_id, or an explicit kind"}
    return run_hypothesis_test(spec, hypothesis_id, HUB, STORE,
                               _panel() if spec.max_symbols is None else None)


def analyze_trade_tool(symbol: str, entry: float | None = None, stop: float | None = None,
                       target: float | None = None, account_equity: float = 100_000.0,
                       risk_pct: float = 1.0, direction: str = "long",
                       strategy_key: str = "sar_v1_1_adr_stop",
                       with_analogs: bool = True) -> dict[str, Any]:
    from .analyzer import TradeIdea, analyze_trade
    return analyze_trade(TradeIdea(symbol=symbol, entry=entry, stop=stop, target=target,
                                   account_equity=account_equity, risk_pct=risk_pct,
                                   direction=direction, strategy_key=strategy_key),
                         HUB, _panel(), with_analogs=with_analogs)


def check_data_quality(symbol: str) -> dict[str, Any]:
    return validate(HUB.daily(symbol)).to_dict()


def get_opening_range(symbol: str, day: str, minutes: int = 5,
                      timeframe: str = "5m") -> dict[str, Any]:
    try:
        tf = Timeframe(timeframe)
        s = HUB.intraday(symbol, tf, _date(day), _date(day))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}
    return opening_range(s.ts, s.high, s.low, _date(day), minutes, tf.minutes)


def system_status() -> dict[str, Any]:
    from ..research.knowledge import knowledge_stats
    from ..risk.guards import GUARDS
    return {
        "providers": HUB.status(),
        "active_daily_provider": HUB.active_daily_provider(),
        "data_note": HUB.data_note(),
        "universe_size": len(HUB.universe()),
        "settings": SETTINGS.redacted(),
        "trading_guards": GUARDS.status(),
        "knowledge": knowledge_stats(STORE),
        "counts": {
            "strategies": len(STORE.strategies()),
            "backtests": len(STORE.backtests(limit=1000)),
            "hypotheses": len(STORE.hypotheses()),
            "experiments": len(STORE.experiments(limit=1000)),
            "trades": len(STORE.trades(limit=10_000)),
        },
        "evidence_classes": dict(EVIDENCE_DESCRIPTIONS),
        "llm_enabled": SETTINGS.llm_enabled,
    }


# ---------------------------------------------------------------------------

def data_status_tool() -> dict[str, Any]:
    """Coverage, freshness, provider health and API usage of the market database."""
    from ..cli import data_status
    d = data_status()
    d["log"] = d["recent_log"][:5]
    d.pop("recent_log", None)
    return d


def screen_market(filters: dict[str, Any] | None = None, preset: str | None = "sar",
                  limit: int = 25, as_of: str | None = None) -> dict[str, Any]:
    """Run the SQL screener over precomputed features. Makes no vendor API calls."""
    from ..screener.sql_screener import SAR_PRESET, run_screen
    f = dict(SAR_PRESET) if preset == "sar" else {}
    f.update(filters or {})
    return run_screen(f, _date(as_of), limit, themes=HUB.themes(), strategy="ai")


def sync_market_data(symbols: list[str] | None = None, timeframe: str = "1d",
                     incremental: bool = True) -> dict[str, Any]:
    """Fetch missing bars into the local store. The only tool that spends API calls."""
    from ..ingest import INGEST
    from ..marketstore.store import MARKET
    target = symbols or MARKET.symbol_list()
    res = (INGEST.incremental_sync(timeframe, target) if incremental
           else INGEST.backfill(target, timeframe))
    res.pop("results", None)
    return res


TOOLS: dict[str, Callable[..., Any]] = {
    "get_market_data": get_market_data,
    "data_status": data_status_tool,
    "screen_market": screen_market,
    "sync_market_data": sync_market_data,
    "get_historical_data": get_historical_data,
    "get_intraday_data": get_intraday_data,
    "get_options_chain": get_options_chain,
    "get_news": get_news,
    "calculate_indicators": calculate_indicators,
    "calculate_relative_strength": calculate_relative_strength,
    "detect_consolidation": detect_consolidation,
    "detect_breakout": detect_breakout,
    "detect_market_regime": detect_market_regime,
    "rank_sectors": rank_sectors_tool,
    "rank_themes": rank_themes_tool,
    "rank_stocks": rank_stocks,
    "find_historical_setups": find_historical_setups_tool,
    "calculate_statistics": calculate_statistics,
    "run_backtest": run_backtest_tool,
    "run_walk_forward_test": run_walk_forward_test,
    "run_monte_carlo": run_monte_carlo_tool,
    "run_sensitivity_analysis": run_sensitivity_analysis,
    "calculate_position_size": calculate_position_size,
    "analyze_options": analyze_options,
    "calculate_risk_reward": calculate_risk_reward,
    "get_trade_history": get_trade_history,
    "analyze_trading_performance": analyze_trading_performance_tool,
    "search_strategy_knowledge": search_strategy_knowledge,
    "search_source_material": search_source_material,
    "create_hypothesis": create_hypothesis_tool,
    "run_hypothesis_test": run_hypothesis_test_tool,
    "analyze_trade": analyze_trade_tool,
    "check_data_quality": check_data_quality,
    "get_opening_range": get_opening_range,
    "system_status": system_status,
}


def tool_catalog() -> list[dict[str, Any]]:
    out = []
    for name, fn in TOOLS.items():
        sig = inspect.signature(fn)
        params = []
        for pname, p in sig.parameters.items():
            params.append({
                "name": pname,
                "required": p.default is inspect.Parameter.empty,
                "default": None if p.default is inspect.Parameter.empty else
                (p.default if isinstance(p.default, (str, int, float, bool, type(None)))
                 else str(p.default)),
                "annotation": (p.annotation if isinstance(p.annotation, str)
                               else getattr(p.annotation, "__name__", str(p.annotation))),
            })
        out.append({"name": name, "doc": (fn.__doc__ or "").strip().split("\n")[0],
                    "parameters": params})
    return out


def call_tool(name: str, **kwargs: Any) -> dict[str, Any]:
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}", "available": sorted(TOOLS)}
    sig = inspect.signature(fn)
    unknown = [k for k in kwargs if k not in sig.parameters]
    if unknown:
        return {"error": f"tool {name} does not accept {unknown}",
                "accepts": sorted(sig.parameters)}
    try:
        result = fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 - tool errors are data, not crashes
        return {"error": f"{type(exc).__name__}: {exc}", "tool": name, "args": kwargs}
    return result if isinstance(result, dict) else {"result": result}
