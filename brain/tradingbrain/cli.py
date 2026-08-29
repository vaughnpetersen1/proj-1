"""Command line entry point.

    python -m tradingbrain.cli serve
    python -m tradingbrain.cli providers
    python -m tradingbrain.cli seed
    python -m tradingbrain.cli scan
    python -m tradingbrain.cli regime
    python -m tradingbrain.cli analyze NVDA
    python -m tradingbrain.cli backtest sar_v1_1_adr_stop --start 2015-01-01
    python -m tradingbrain.cli research run --feature volume_contraction
    python -m tradingbrain.cli research refresh
    python -m tradingbrain.cli ask "what is the market regime?"
    python -m tradingbrain.cli demo          # seed everything and run the suite
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Any


def _print(obj: Any) -> None:
    from .api.app import _json_default
    print(json.dumps(obj, indent=2, default=_json_default)[:200_000])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tradingbrain", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host"); s.add_argument("--port", type=int)

    sub.add_parser("providers", help="which data providers are reachable")
    sub.add_parser("status", help="full system status")
    sub.add_parser("seed", help="seed knowledge base, concepts, hypotheses, strategies")
    sub.add_parser("regime", help="classify the market regime")
    sub.add_parser("sectors", help="rank sectors and themes")

    sc = sub.add_parser("scan", help="run the daily scanner")
    sc.add_argument("--limit", type=int, default=15)
    sc.add_argument("--strategy", default="sar_v1_1_adr_stop")

    an = sub.add_parser("analyze", help="analyze a trade idea")
    an.add_argument("symbol")
    an.add_argument("--entry", type=float); an.add_argument("--stop", type=float)
    an.add_argument("--target", type=float)
    an.add_argument("--equity", type=float, default=100_000)
    an.add_argument("--risk", type=float, default=1.0)
    an.add_argument("--no-analogs", action="store_true")

    bt = sub.add_parser("backtest", help="backtest a strategy from the library")
    bt.add_argument("strategy", nargs="?", default="sar_v1_1_adr_stop")
    bt.add_argument("--start"); bt.add_argument("--end")
    bt.add_argument("--max-symbols", type=int)
    bt.add_argument("--montecarlo", action="store_true")

    rs = sub.add_parser("research", help="research lab")
    rsub = rs.add_subparsers(dest="rcmd", required=True)
    rr = rsub.add_parser("run", help="run a hypothesis test")
    rr.add_argument("--question"); rr.add_argument("--kind", default="split")
    rr.add_argument("--feature"); rr.add_argument("--concept")
    rr.add_argument("--start", default="2012-01-01")
    rsub.add_parser("dashboard", help="research dashboard")
    rsub.add_parser("conflicts", help="knowledge conflicts")
    rsub.add_parser("refresh", help="re-fetch source material (needs network egress)")
    rw = rsub.add_parser("why", help="provenance chain for a concept")
    rw.add_argument("concept")

    a = sub.add_parser("ask", help="ask the AI Brain")
    a.add_argument("question", nargs="+")
    a.add_argument("--mode", choices=["router", "llm"])

    al = sub.add_parser("alerts", help="evaluate alerts")
    al.add_argument("--evaluate", action="store_true")

    dt_ = sub.add_parser("data", help="market-data infrastructure")
    dsub = dt_.add_subparsers(dest="dcmd", required=True)
    dsub.add_parser("status", help="providers, coverage, freshness, quality, API usage")
    db = dsub.add_parser("bootstrap", help="universe + history + features + context")
    db.add_argument("--years", type=float)
    db.add_argument("--symbols", help="comma-separated; default is the whole universe")
    db.add_argument("--max-symbols", type=int)
    du = dsub.add_parser("universe", help="refresh the tradeable symbol list")
    du.add_argument("--provider")
    du.add_argument("--max-symbols", type=int)
    dbf = dsub.add_parser("backfill", help="fetch only the bars the store is missing")
    dbf.add_argument("symbols", nargs="*", help="blank = every symbol in the universe")
    dbf.add_argument("--timeframe", default="1d")
    dbf.add_argument("--start"); dbf.add_argument("--end")
    dbf.add_argument("--provider"); dbf.add_argument("--force", action="store_true")
    dsy = dsub.add_parser("sync", help="incremental: only what happened since the last bar")
    dsy.add_argument("--timeframe", default="1d")
    dsy.add_argument("--symbols")
    dfe = dsub.add_parser("features", help="recompute indicators and strategy features")
    dfe.add_argument("--symbols"); dfe.add_argument("--bars", type=int,
                                                    help="only recompute the last N bars")
    dsub.add_parser("context", help="compute and store market regime + sector ranks")
    dctx = dsub.add_parser("context-history", help="backfill stored regime history")
    dctx.add_argument("--start", default="2015-01-01")
    dctx.add_argument("--step", type=int, default=5)
    dv = dsub.add_parser("validate", help="re-run every quality check over the store")
    dv.add_argument("--timeframe", default="1d")
    dca = dsub.add_parser("corporate-actions", help="fetch splits and dividends")
    dca.add_argument("symbols", nargs="*")
    dop = dsub.add_parser("options", help="snapshot an options chain into the store")
    dop.add_argument("symbol")
    dsc = dsub.add_parser("screen", help="run the SQL screener over stored features")
    dsc.add_argument("--preset", default="sar", choices=["sar", "none"])
    dsc.add_argument("--limit", type=int, default=20)
    dsub.add_parser("datasets", help="list locked dataset versions")
    dcl = dsub.add_parser("clear", help="drop derived tables (raw data is untouched)")
    dcl.add_argument("--what", default="all",
                     choices=["all", "indicators", "features", "regimes", "sectors", "scans"])
    drt = dsub.add_parser("stream", help="run the realtime ingestion service")
    drt.add_argument("--symbols")
    drt.add_argument("--seconds", type=int, default=0, help="0 = run until interrupted")

    sub.add_parser("demo", help="seed and exercise every engine end to end")

    dj = sub.add_parser("demo-journal",
                        help="populate the journal from a backtest, clearly tagged SAMPLE")
    dj.add_argument("--strategy", default="sar_v1_1_adr_stop")
    dj.add_argument("--start", default="2024-01-01")
    dj.add_argument("--limit", type=int, default=60)
    dj.add_argument("--clear", action="store_true", help="remove existing SAMPLE trades first")

    args = p.parse_args(argv)

    if args.cmd == "serve":
        from .api.app import serve
        serve(args.host, args.port)
        return 0

    if args.cmd == "providers":
        from .data.providers.registry import HUB
        _print({"providers": HUB.status(), "active": HUB.active_daily_provider(),
                "note": HUB.data_note(), "universe_size": len(HUB.universe())})
        return 0

    if args.cmd == "status":
        from .ai.tools import system_status
        _print(system_status())
        return 0

    if args.cmd == "seed":
        _print(seed_all())
        return 0

    if args.cmd == "regime":
        from .ai.tools import detect_market_regime
        _print(detect_market_regime())
        return 0

    if args.cmd == "sectors":
        from .ai.tools import rank_sectors_tool, rank_themes_tool
        _print({"sectors": rank_sectors_tool()["ranks"][:8],
                "themes": rank_themes_tool()["ranks"][:8]})
        return 0

    if args.cmd == "scan":
        from .ai.tools import rank_stocks
        r = rank_stocks(limit=args.limit, strategy_key=args.strategy)
        _print({k: v for k, v in r.items() if k != "market_regime"})
        return 0

    if args.cmd == "analyze":
        from .ai.tools import analyze_trade_tool
        _print(analyze_trade_tool(symbol=args.symbol, entry=args.entry, stop=args.stop,
                                  target=args.target, account_equity=args.equity,
                                  risk_pct=args.risk, with_analogs=not args.no_analogs))
        return 0

    if args.cmd == "backtest":
        from .ai.tools import run_backtest_tool, run_monte_carlo_tool
        r = run_backtest_tool(args.strategy, args.start, args.end,
                              max_symbols=args.max_symbols)
        _print(r)
        if args.montecarlo and r.get("backtest_id"):
            _print(run_monte_carlo_tool(backtest_id=r["backtest_id"]))
        return 0

    if args.cmd == "research":
        if args.rcmd == "run":
            from .ai.tools import run_hypothesis_test_tool
            _print(run_hypothesis_test_tool(question=args.question, kind=args.kind,
                                            feature=args.feature, concept=args.concept,
                                            start=args.start))
        elif args.rcmd == "dashboard":
            from .research.hypothesis import research_dashboard
            _print(research_dashboard())
        elif args.rcmd == "conflicts":
            from .research.conflicts import conflict_report
            _print(conflict_report())
        elif args.rcmd == "why":
            from .research.knowledge import why_rule
            _print(why_rule(args.concept))
        elif args.rcmd == "refresh":
            from .research.sources.refresh import refresh_sources
            _print(refresh_sources())
        return 0

    if args.cmd == "ask":
        from .ai.orchestrator import ORCHESTRATOR
        ans = ORCHESTRATOR.ask(" ".join(args.question), args.mode)
        print(ans.text())
        if ans.warnings:
            print("\nWARNINGS")
            for w in ans.warnings:
                print(f"  ! {w}")
        return 0

    if args.cmd == "alerts":
        from .alerts.engine import evaluate_alerts, list_alerts
        _print(evaluate_alerts() if args.evaluate else list_alerts())
        return 0

    if args.cmd == "data":
        return _data_command(args)

    if args.cmd == "demo":
        _print(demo())
        return 0

    if args.cmd == "demo-journal":
        _print(demo_journal(args.strategy, args.start, args.limit, args.clear))
        return 0

    return 1


def _data_command(args) -> int:
    import datetime as _dt
    from .ingest import INGEST, FEATURES, REALTIME, backfill_context, refresh_context
    from .marketstore.store import MARKET
    from .screener.sql_screener import SAR_PRESET, run_screen

    def syms(raw: str | None) -> list[str] | None:
        return [s.strip().upper() for s in raw.split(",") if s.strip()] if raw else None

    if args.dcmd == "status":
        _print(data_status())
    elif args.dcmd == "bootstrap":
        _print(bootstrap_data(years=args.years, symbols=syms(args.symbols),
                              max_symbols=args.max_symbols))
    elif args.dcmd == "universe":
        _print(INGEST.sync_universe(args.provider, args.max_symbols))
    elif args.dcmd == "backfill":
        target = [s.upper() for s in args.symbols] or MARKET.symbol_list()
        _print(INGEST.backfill(target, args.timeframe,
                               _dt.date.fromisoformat(args.start) if args.start else None,
                               _dt.date.fromisoformat(args.end) if args.end else None,
                               args.provider, args.force))
    elif args.dcmd == "sync":
        _print(INGEST.incremental_sync(args.timeframe, syms(args.symbols)))
    elif args.dcmd == "features":
        _print(FEATURES.compute_universe(syms(args.symbols), lookback_bars=args.bars))
    elif args.dcmd == "context":
        _print(refresh_context())
    elif args.dcmd == "context-history":
        _print(backfill_context(_dt.date.fromisoformat(args.start), step_days=args.step))
    elif args.dcmd == "validate":
        _print(INGEST.validate_store(args.timeframe))
    elif args.dcmd == "corporate-actions":
        _print(INGEST.sync_corporate_actions(
            [s.upper() for s in args.symbols] or MARKET.symbol_list()))
    elif args.dcmd == "options":
        _print(INGEST.sync_options(args.symbol))
    elif args.dcmd == "screen":
        filters = dict(SAR_PRESET) if args.preset == "sar" else {"min_price": 1.0}
        r = run_screen(filters, limit=args.limit, strategy="cli")
        _print({k: v for k, v in r.items() if k != "results"})
        for row in r.get("results", []):
            print(f"  {row['symbol']:6s} q={row.get('base_quality')} "
                  f"prior={row.get('prior_move_pct')} dist={row.get('breakout_distance_pct')}")
    elif args.dcmd == "datasets":
        _print(MARKET.dataset_versions())
    elif args.dcmd == "clear":
        _print(MARKET.clear_derived(args.what))
    elif args.dcmd == "stream":
        res = REALTIME.start(syms(args.symbols))
        _print(res)
        if res.get("ok"):
            import time as _t
            try:
                deadline = _t.time() + args.seconds if args.seconds else None
                while REALTIME.is_running():
                    _t.sleep(2)
                    if deadline and _t.time() > deadline:
                        break
            except KeyboardInterrupt:
                pass
            _print(REALTIME.stop())
    return 0


def data_status() -> dict[str, Any]:
    """Everything the Data Control Center shows, also available from the shell."""
    from .data.providers.registry import HUB, provider_status
    from .ingest import REALTIME
    from .marketstore.store import MARKET
    stats = MARKET.stats()
    return {
        "providers": provider_status(),
        "active_read_path": HUB.active_daily_provider(),
        "data_note": HUB.data_note(),
        "freshness": MARKET.freshness("1d"),
        "database": stats,
        "features": MARKET.feature_coverage(),
        "sync": MARKET.sync_status(limit=25),
        "quality_flags": MARKET.flags(limit=25),
        "api_usage_24h": MARKET.api_usage(24),
        "realtime": REALTIME.status(),
        "recent_log": MARKET.logs(limit=25),
        "datasets": MARKET.dataset_versions(10),
    }


def bootstrap_data(years: float | None = None, symbols: list[str] | None = None,
                   max_symbols: int | None = None) -> dict[str, Any]:
    """Universe -> history -> features -> context, in one call.

    Every step is incremental, so running it twice costs almost nothing: the
    backfill asks only for missing ranges and the feature pass overwrites the
    same rows.
    """
    import time as _t
    from .ingest import INGEST, FEATURES, refresh_context
    from .marketstore.store import MARKET
    t0 = _t.time()
    out: dict[str, Any] = {}
    out["universe"] = INGEST.sync_universe(max_symbols=max_symbols)
    target = symbols or MARKET.symbol_list()
    if max_symbols:
        target = target[:max_symbols]
    start = INGEST.default_start(years)
    out["backfill"] = {k: v for k, v in INGEST.backfill(target, "1d", start).items()
                       if k != "results"}
    out["features"] = {k: v for k, v in
                       FEATURES.compute_universe(target).items() if k != "skipped_detail"}
    out["context"] = refresh_context()
    out["database"] = MARKET.stats()
    out["seconds"] = round(_t.time() - t0, 1)
    out["next"] = ("`cli data status` for coverage and freshness; "
                   "`cli data screen` to run the SQL screener; "
                   "`cli serve` for the web app.")
    return out


def seed_all() -> dict[str, Any]:
    """Populate the knowledge base, concept library, hypotheses and strategy versions."""
    from .db.store import STORE
    from .research.formalizer import seed_concepts
    from .research.hypothesis import seed_hypotheses
    from .research.knowledge import seed_knowledge_base
    from .strategy.library import LIBRARY, get_strategy
    out = {"knowledge": seed_knowledge_base(STORE), "concepts": seed_concepts(STORE),
           "hypotheses": seed_hypotheses(STORE), "strategies": []}
    for key in LIBRARY:
        s = get_strategy(key)
        sid = STORE.save_strategy(s.name, s.version, s.to_dict(),
                                  change_reason="seeded from tradingbrain.strategy.library",
                                  notes=s.description, approved=False)
        out["strategies"].append({"key": key, "id": sid, "name": s.name,
                                  "version": s.version})
    return out


def demo_journal(strategy_key: str = "sar_v1_1_adr_stop", start: str = "2024-01-01",
                 limit: int = 60, clear: bool = False) -> dict[str, Any]:
    """Fill the journal with SIMULATED trades so the analytics have something to chew on.

    These are real outputs of the backtester, not invented numbers -- but they are
    simulated, on whatever data path is active, and every row says so in its notes
    and carries the tag ``SAMPLE``. Delete them before logging real trades:
    behavioural findings computed over a mix of real and simulated trades would be
    meaningless.
    """
    from .backtest.engine import Backtester
    from .db.store import STORE
    from .strategy.library import get_strategy
    import datetime as _dt

    if clear:
        STORE.execute("DELETE FROM trades WHERE tags LIKE '%SAMPLE%'")
    spec = get_strategy(strategy_key)
    res = Backtester(spec, start=_dt.date.fromisoformat(start)).run()
    note = (f"SAMPLE -- simulated by the backtester ({spec.name} v{spec.version}) on "
            f"{res.data_origin} data from provider '{res.data_provider}'. NOT a real trade. "
            "Delete before logging real trades.")
    added = 0
    for t in res.trades[-limit:]:
        STORE.add_trade(
            ticker=t.symbol, direction=t.direction, strategy=f"{spec.name} v{spec.version}",
            setup="breakout", entry_date=t.entry_date, entry_price=round(t.entry_price, 4),
            stop_price=round(t.initial_stop, 4), position_size=t.shares,
            exit_date=t.exit_date, exit_price=round(t.avg_exit_price or 0.0, 4),
            pnl=round(t.pnl, 2), r_multiple=round(t.r_multiple, 4),
            fees=round(t.commission, 2), instrument="stock",
            market_regime=t.market_regime, sector=t.sector,
            reason_entry="qualifying breakout under the strategy definition",
            reason_exit=t.exit_reason,
            account_equity_at_entry=spec.risk.account_equity,
            thesis="simulated", tags="SAMPLE,simulated", notes=note)
        added += 1
    STORE.audit("system", "demo_journal", {"added": added, "origin": res.data_origin})
    return {"added": added, "strategy": f"{spec.name} v{spec.version}",
            "data_origin": res.data_origin, "warning": note}


def demo() -> dict[str, Any]:
    """Exercise every major engine once and report what happened."""
    import time
    from .ai.tools import (analyze_trade_tool, detect_market_regime, rank_sectors_tool,
                           rank_stocks, run_backtest_tool, run_monte_carlo_tool,
                           run_hypothesis_test_tool)
    out: dict[str, Any] = {"steps": []}

    def step(name: str, fn):
        t = time.time()
        try:
            r = fn()
            ok, detail = True, None
        except Exception as exc:  # noqa: BLE001
            r, ok, detail = None, False, f"{type(exc).__name__}: {exc}"
        out["steps"].append({"step": name, "ok": ok, "seconds": round(time.time() - t, 2),
                             "error": detail, "summary": _summarise(name, r)})
        return r

    step("seed", seed_all)
    step("regime", lambda: detect_market_regime(with_breadth=True))
    step("sectors", rank_sectors_tool)
    step("scan", lambda: rank_stocks(limit=10))
    bt = step("backtest", lambda: run_backtest_tool("sar_v1_1_adr_stop", "2015-01-01"))
    step("montecarlo", lambda: run_monte_carlo_tool(
        backtest_id=(bt or {}).get("backtest_id"), simulations=2000))
    step("hypothesis", lambda: run_hypothesis_test_tool(
        kind="split", feature="volume_contraction", start="2012-01-01"))
    step("analyze", lambda: analyze_trade_tool(symbol="NVDA", with_analogs=True))
    out["ok"] = all(s["ok"] for s in out["steps"])
    return out


def _summarise(name: str, r: Any) -> Any:
    if not isinstance(r, dict):
        return None
    if name == "regime":
        return {k: r.get(k) for k in ("label", "volatility_label", "strength", "filter_pass")}
    if name == "sectors":
        return [g["name"] for g in (r.get("ranks") or [])[:3]]
    if name == "scan":
        return {"found": r.get("candidates_found"),
                "top": [(c["symbol"], c["score"]) for c in (r.get("results") or [])[:5]]}
    if name == "backtest":
        m = r.get("metrics") or {}
        return {k: m.get(k) for k in ("n_trades", "win_rate_pct", "expectancy_r",
                                      "profit_factor", "max_drawdown_pct")}
    if name == "montecarlo":
        return {"median_return_pct": (r.get("total_return_pct") or {}).get("p50"),
                "prob_decline_pct": r.get("prob_decline_pct")} if r.get("ok") else r.get("reason")
    if name == "hypothesis":
        return {"verdict": r.get("verdict"), "n": r.get("sample_size")}
    if name == "analyze":
        return (r.get("verdict") or {}).get("label")
    if name == "seed":
        return {"knowledge": (r.get("knowledge") or {}).get("knowledge_items"),
                "strategies": len(r.get("strategies") or [])}
    return None


if __name__ == "__main__":
    sys.exit(main())
