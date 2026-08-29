"""HTTP API and static file server.

Stdlib only, on purpose: numpy is the single hard dependency of this project, so
the whole system runs after ``pip install numpy`` with nothing else to break.
The router is small enough to read in one sitting.
"""

from __future__ import annotations

import datetime as dt
import json
import mimetypes
import pathlib
import re
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from ..config import SETTINGS
from ..db.store import STORE

WEB_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent / "web"

Handler = Callable[[dict[str, Any], dict[str, Any]], Any]
ROUTES: list[tuple[str, re.Pattern[str], Handler]] = []


def route(method: str, pattern: str):
    def deco(fn: Handler) -> Handler:
        ROUTES.append((method, re.compile(f"^{pattern}$"), fn))
        return fn
    return deco


def _date(v: Any) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _f(q: dict, key: str, default: float | None = None) -> float | None:
    v = q.get(key)
    if v in (None, ""):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(q: dict, key: str, default: int | None = None) -> int | None:
    v = _f(q, key, None)
    return int(v) if v is not None else default


def _b(q: dict, key: str, default: bool = False) -> bool:
    v = q.get(key)
    if v is None:
        return default
    return str(v).lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# system
# ---------------------------------------------------------------------------

@route("GET", r"/api/status")
def _status(q, body):
    from ..ai.tools import system_status
    return system_status()


@route("GET", r"/api/dashboard")
def _dashboard(q, body):
    from ..ai.tools import _panel
    from ..data.providers.registry import HUB
    from ..journal.analytics import journal_summary
    from ..regime.classifier import classify_market
    from ..research.hypothesis import research_dashboard
    from ..scanner.scan import ScanConfig, run_scan
    from ..sectors.ranking import rank_sectors, rank_themes
    as_of = _date(q.get("as_of"))
    panel = _panel()
    regime = classify_market(as_of, HUB, with_breadth=True)
    scan = run_scan(ScanConfig(as_of=as_of, limit=_i(q, "limit", 12),
                               strategy_key=q.get("strategy", "sar_v1_1_adr_stop")),
                    HUB, panel)
    rd = research_dashboard(STORE)
    return {
        "as_of": scan.get("as_of") or regime.as_of,
        "data_note": HUB.data_note(),
        "data_origin": panel.origin, "data_provider": panel.provider,
        "market": {**regime.to_dict(), "claim": regime.claim().to_dict()},
        "sectors": rank_sectors(as_of, HUB, panel)["ranks"][:6],
        "themes": rank_themes(as_of, HUB, panel)["ranks"][:6],
        "scan": scan,
        "journal": journal_summary(STORE),
        "research": {"counts": rd["counts"],
                     "open_hypotheses": len(rd["active_hypotheses"]),
                     "supported": len(rd["supported"]), "mixed": len(rd["mixed"]),
                     "unsupported": len(rd["unsupported"]),
                     "insufficient": len(rd["insufficient"])},
        "alerts": STORE.alert_events(10),
    }


@route("GET", r"/api/providers")
def _providers(q, body):
    from ..data.providers.registry import HUB
    return {"providers": HUB.status(), "active": HUB.active_daily_provider(),
            "note": HUB.data_note(), "universe": HUB.universe()}


# ---------------------------------------------------------------------------
# market data
# ---------------------------------------------------------------------------

@route("GET", r"/api/market/([A-Za-z0-9._-]+)")
def _market(q, body):
    from ..ai.tools import calculate_indicators, get_market_data
    sym = body["_path"][0]
    data = get_market_data(sym, _i(q, "bars", 260) or 260)
    if data.get("error"):
        return data
    data["indicators"] = calculate_indicators(sym).get("indicators")
    from ..ai.tools import check_data_quality, detect_consolidation
    data["quality"] = check_data_quality(sym)
    data["consolidation"] = detect_consolidation(sym)
    return data


@route("GET", r"/api/intraday/([A-Za-z0-9._-]+)")
def _intraday(q, body):
    from ..ai.tools import get_intraday_data
    return get_intraday_data(body["_path"][0], q.get("timeframe", "5m"),
                             q.get("start"), q.get("end"))


@route("GET", r"/api/regime")
def _regime(q, body):
    from ..ai.tools import detect_market_regime
    return detect_market_regime(q.get("as_of"), _b(q, "breadth", True))


@route("GET", r"/api/sectors")
def _sectors(q, body):
    from ..ai.tools import rank_sectors_tool
    return rank_sectors_tool(q.get("as_of"))


@route("GET", r"/api/themes")
def _themes(q, body):
    from ..ai.tools import rank_themes_tool
    return rank_themes_tool(q.get("as_of"))


@route("GET", r"/api/scan")
def _scan(q, body):
    from ..ai.tools import _panel
    from ..data.providers.registry import HUB
    from ..scanner.scan import ScanConfig, run_scan
    cfg = ScanConfig(strategy_key=q.get("strategy", "sar_v1_1_adr_stop"),
                     as_of=_date(q.get("as_of")), limit=_i(q, "limit", 50) or 50,
                     include_pre_breakout=_b(q, "pre_breakout", True))
    res = run_scan(cfg, HUB, _panel())
    if _b(q, "save", False) and res.get("ok"):
        STORE.save_scan(res["as_of"], {"strategy": cfg.strategy_key}, res["results"],
                        res["data_origin"])
    return res


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------

@route("POST", r"/api/analyze")
def _analyze(q, body):
    from ..ai.tools import analyze_trade_tool
    return analyze_trade_tool(
        symbol=body.get("symbol", ""), entry=body.get("entry"), stop=body.get("stop"),
        target=body.get("target"),
        account_equity=float(body.get("account_equity", 100_000)),
        risk_pct=float(body.get("risk_pct", 1.0)),
        direction=body.get("direction", "long"),
        strategy_key=body.get("strategy_key", "sar_v1_1_adr_stop"),
        with_analogs=bool(body.get("with_analogs", True)))


# ---------------------------------------------------------------------------
# strategies & backtesting
# ---------------------------------------------------------------------------

@route("GET", r"/api/strategies")
def _strategies(q, body):
    from ..strategy.library import list_strategies
    return {"library": list_strategies(), "saved": STORE.strategies()}


@route("GET", r"/api/strategies/([A-Za-z0-9_.-]+)")
def _strategy(q, body):
    from ..strategy.library import get_strategy
    try:
        s = get_strategy(body["_path"][0])
    except KeyError as exc:
        return {"error": str(exc)}
    return {"key": body["_path"][0], "spec": s.to_dict(), "fingerprint": s.fingerprint()}


@route("POST", r"/api/strategies/save")
def _save_strategy(q, body):
    from ..strategy.spec import SpecError, StrategySpec
    try:
        spec = StrategySpec.from_dict(body.get("spec") or {})
    except SpecError as exc:
        return {"ok": False, "error": str(exc)}
    sid = STORE.save_strategy(spec.name, spec.version, spec.to_dict(),
                              change_reason=body.get("change_reason", ""),
                              notes=body.get("notes", ""),
                              approved=False)
    return {"ok": True, "id": sid, "name": spec.name, "version": spec.version,
            "fingerprint": spec.fingerprint(),
            "note": "Saved unapproved. Versions are immutable: to change rules, bump the "
                    "version rather than editing this one."}


@route("POST", r"/api/backtest")
def _backtest(q, body):
    from ..backtest.engine import Backtester
    from ..data.panel import build_panel
    from ..data.providers.registry import HUB
    from ..strategy.library import get_strategy
    from ..strategy.spec import SpecError, StrategySpec
    try:
        spec = (StrategySpec.from_dict(body["spec"]) if body.get("spec")
                else get_strategy(body.get("strategy_key", "sar_v1_1_adr_stop")))
    except (SpecError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
    start, end = _date(body.get("start")), _date(body.get("end"))
    max_symbols = body.get("max_symbols")
    panel = build_panel(body.get("symbols"), HUB, start, end,
                        max_symbols=int(max_symbols) if max_symbols else None)
    r = Backtester(spec, HUB, start, end, panel=panel).run()
    bid = STORE.save_backtest(label=f"{spec.name} v{spec.version}", spec=spec.to_dict(),
                              metrics=r.metrics,
                              trades=[t.to_dict() for t in r.trades][:3000],
                              equity_curve=r.equity_curve, data_origin=r.data_origin,
                              data_provider=r.data_provider, warnings=r.warnings,
                              runtime_seconds=r.runtime_seconds)
    d = r.to_dict()
    d["backtest_id"] = bid
    d["claim"] = r.claim().to_dict()
    d["trades"] = d["trades"][:500]
    return d


@route("GET", r"/api/backtests")
def _backtests(q, body):
    return {"backtests": STORE.backtests(limit=_i(q, "limit", 50) or 50)}


@route("GET", r"/api/backtests/(\d+)")
def _backtest_one(q, body):
    r = STORE.backtest(int(body["_path"][0]))
    return r or {"error": "not found"}


@route("POST", r"/api/walkforward")
def _walkforward(q, body):
    from ..ai.tools import run_walk_forward_test
    return run_walk_forward_test(
        strategy_key=body.get("strategy_key", "sar_v1_0"),
        parameter=body.get("parameter", "setup.prior_move_min_pct"),
        values=body.get("values"), start=body.get("start", "2012-01-01"),
        end=body.get("end"), train_years=float(body.get("train_years", 4)),
        test_years=float(body.get("test_years", 1)),
        objective=body.get("objective", "expectancy_r"),
        max_symbols=body.get("max_symbols", 40))


@route("POST", r"/api/montecarlo")
def _montecarlo(q, body):
    from ..ai.tools import run_monte_carlo_tool
    return run_monte_carlo_tool(backtest_id=body.get("backtest_id"),
                                r_multiples=body.get("r_multiples"),
                                simulations=int(body.get("simulations", 5000)),
                                risk_pct=float(body.get("risk_pct", 1.0)),
                                method=body.get("method", "bootstrap"))


@route("POST", r"/api/sensitivity")
def _sensitivity(q, body):
    from ..ai.tools import run_sensitivity_analysis
    return run_sensitivity_analysis(
        strategy_key=body.get("strategy_key", "sar_v1_0"),
        parameter=body.get("parameter", "setup.prior_move_min_pct"),
        values=body.get("values"), start=body.get("start", "2015-01-01"),
        end=body.get("end"), max_symbols=body.get("max_symbols", 40))


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------

@route("GET", r"/api/research")
def _research(q, body):
    from ..research.hypothesis import research_dashboard
    return research_dashboard(STORE)


@route("GET", r"/api/research/experiments/(\d+)")
def _experiment(q, body):
    return STORE.experiment(int(body["_path"][0])) or {"error": "not found"}


@route("POST", r"/api/research/hypothesis")
def _create_hypothesis(q, body):
    from ..research.hypothesis import create_hypothesis
    return create_hypothesis(body.get("question", ""), body.get("statement", ""),
                             body.get("category", ""), store=STORE)


@route("POST", r"/api/research/run")
def _run_hypothesis(q, body):
    from ..ai.tools import run_hypothesis_test_tool
    return run_hypothesis_test_tool(
        question=body.get("question"), hypothesis_id=body.get("hypothesis_id"),
        kind=body.get("kind"), feature=body.get("feature"), concept=body.get("concept"),
        horizon=int(body.get("horizon", 10)), start=body.get("start", "2012-01-01"),
        max_symbols=body.get("max_symbols"))


@route("GET", r"/api/research/features")
def _features(q, body):
    from ..research.hypothesis import SPLIT_FEATURES
    return {"split_features": {k: {kk: vv for kk, vv in v.items() if kk != "getter"}
                               for k, v in SPLIT_FEATURES.items()}}


@route("GET", r"/api/knowledge")
def _knowledge(q, body):
    from ..research.knowledge import knowledge_stats, search_knowledge
    return {"items": search_knowledge(q.get("q"), q.get("category"), STORE),
            "stats": knowledge_stats(STORE), "sources": STORE.sources()}


@route("GET", r"/api/knowledge/why")
def _why(q, body):
    from ..research.knowledge import why_rule
    return why_rule(q.get("concept", ""), STORE)


@route("GET", r"/api/concepts")
def _concepts(q, body):
    from ..research.formalizer import CONCEPTS
    return {"concepts": CONCEPTS, "stored": STORE.concepts()}


@route("GET", r"/api/conflicts")
def _conflicts(q, body):
    from ..research.conflicts import conflict_report
    return conflict_report(STORE)


# ---------------------------------------------------------------------------
# options / risk
# ---------------------------------------------------------------------------

@route("GET", r"/api/options/([A-Za-z0-9._-]+)")
def _chain(q, body):
    from ..options.chain import expirations, get_chain
    sym = body["_path"][0]
    ch = get_chain(sym, q.get("expiration"))
    ch["expirations"] = expirations(sym)
    return ch


@route("POST", r"/api/options/compare")
def _compare(q, body):
    from ..options.analysis import compare_contracts
    return compare_contracts(body.get("symbol", ""), body.get("contracts") or [],
                             move_pct=float(body.get("move_pct", 10)),
                             hold_days=int(body.get("hold_days", 10)))


@route("POST", r"/api/options/scenario")
def _scenario(q, body):
    from ..options.analysis import scenario_matrix
    return scenario_matrix(
        underlying=float(body["underlying"]), strike=float(body["strike"]),
        dte=int(body["dte"]), premium=float(body["premium"]),
        iv_pct=float(body.get("iv_pct", 35)), option_type=body.get("type", "call"),
        contracts=int(body.get("contracts", 1)), prices=body.get("prices"),
        days_forward=body.get("days_forward"), iv_shifts_pct=body.get("iv_shifts_pct"))


@route("POST", r"/api/risk/size")
def _size(q, body):
    from ..risk.sizing import portfolio_check, position_size, risk_reward
    p = position_size(float(body.get("account_equity", 100_000)),
                      float(body.get("risk_pct", 1.0)), float(body["entry"]),
                      float(body["stop"]),
                      float(body["target"]) if body.get("target") else None,
                      body.get("direction", "long"),
                      max_position_pct=body.get("max_position_pct"),
                      adr_pct=body.get("adr_pct"),
                      max_stop_adr_multiple=body.get("max_stop_adr_multiple"))
    out = p.to_dict()
    if body.get("target"):
        out["risk_reward"] = risk_reward(float(body["entry"]), float(body["stop"]),
                                         float(body["target"]), body.get("direction", "long"))
    if body.get("positions"):
        out["portfolio"] = portfolio_check(body["positions"],
                                           float(body.get("account_equity", 100_000)))
    return out


@route("POST", r"/api/risk/options")
def _size_options(q, body):
    from ..risk.sizing import option_position_size
    return option_position_size(float(body.get("account_equity", 100_000)),
                                float(body.get("risk_pct", 1.0)),
                                float(body["premium"]))


@route("GET", r"/api/risk/guards")
def _guards(q, body):
    from ..risk.guards import GUARDS
    return GUARDS.status()


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------

@route("GET", r"/api/journal")
def _journal(q, body):
    from ..journal.analytics import journal_summary
    return {"summary": journal_summary(STORE),
            "trades": STORE.trades(limit=_i(q, "limit", 500) or 500)}


@route("POST", r"/api/journal/trade")
def _add_trade(q, body):
    if body.get("id"):
        tid = int(body.pop("id"))
        STORE.update_trade(tid, **body)
        return {"ok": True, "id": tid, "updated": True}
    allowed = {"ticker", "direction", "strategy", "setup", "entry_date", "entry_price",
               "stop_price", "target_price", "position_size", "exit_date", "exit_price",
               "pnl", "r_multiple", "fees", "instrument", "option_details",
               "screenshot_path", "thesis", "market_regime", "sector", "ai_analysis",
               "reason_entry", "reason_exit", "mistakes", "notes", "tags",
               "account_equity_at_entry"}
    data = {k: v for k, v in body.items() if k in allowed and v not in ("", None)}
    if not data.get("ticker"):
        return {"ok": False, "error": "ticker is required"}
    # derive P/L and R when the operator supplies the inputs but not the results
    try:
        if "pnl" not in data and all(k in data for k in ("entry_price", "exit_price",
                                                          "position_size")):
            sign = -1.0 if data.get("direction") == "short" else 1.0
            data["pnl"] = sign * (float(data["exit_price"]) - float(data["entry_price"])) \
                * float(data["position_size"])
        if "r_multiple" not in data and "pnl" in data and \
                all(k in data for k in ("entry_price", "stop_price", "position_size")):
            risk = abs(float(data["entry_price"]) - float(data["stop_price"])) \
                * float(data["position_size"])
            if risk > 0:
                data["r_multiple"] = float(data["pnl"]) / risk
    except (TypeError, ValueError):
        pass
    return {"ok": True, "id": STORE.add_trade(**data)}


@route("POST", r"/api/journal/import")
def _import(q, body):
    from ..journal.analytics import import_trades_csv
    return import_trades_csv(body.get("csv", ""), STORE)


@route("POST", r"/api/journal/delete")
def _delete_trade(q, body):
    STORE.delete_trade(int(body["id"]))
    return {"ok": True}


@route("GET", r"/api/journal/analysis")
def _journal_analysis(q, body):
    from ..journal.analytics import analyze_trading_performance
    return analyze_trading_performance(STORE)


# ---------------------------------------------------------------------------
# alerts / ai / tools
# ---------------------------------------------------------------------------

@route("GET", r"/api/alerts")
def _alerts(q, body):
    from ..alerts.engine import alert_history, list_alerts
    return {**list_alerts(), "events": alert_history(_i(q, "limit", 50) or 50)}


@route("POST", r"/api/alerts")
def _create_alert(q, body):
    from ..alerts.engine import create_alert
    return create_alert(body.get("kind", ""), body.get("condition") or {},
                        body.get("symbol"), body.get("channels") or ["inapp"])


@route("POST", r"/api/alerts/evaluate")
def _eval_alerts(q, body):
    from ..alerts.engine import evaluate_alerts
    return evaluate_alerts(_date(body.get("as_of")))


@route("POST", r"/api/ai/ask")
def _ask(q, body):
    from ..ai.orchestrator import ORCHESTRATOR
    return ORCHESTRATOR.ask(body.get("question", ""), body.get("mode"),
                            body.get("context")).to_dict()


@route("GET", r"/api/ai/modes")
def _modes(q, body):
    from ..ai.orchestrator import ORCHESTRATOR
    return ORCHESTRATOR.available_modes()


@route("GET", r"/api/tools")
def _tools(q, body):
    from ..ai.tools import tool_catalog
    return {"tools": tool_catalog()}


@route("POST", r"/api/tools/([A-Za-z_0-9]+)")
def _call_tool(q, body):
    from ..ai.tools import call_tool
    args = {k: v for k, v in body.items() if not k.startswith("_")}
    return call_tool(body["_path"][0], **args)


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "TradingBrain/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:      # quieter logs
        if SETTINGS.__dict__.get("verbose"):
            super().log_message(fmt, *args)

    # -- helpers -----------------------------------------------------------
    def _send(self, status: int, payload: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, obj: Any) -> None:
        self._send(status, json.dumps(obj, default=_json_default).encode(),
                   "application/json; charset=utf-8")

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = (WEB_ROOT / rel).resolve()
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
            target = WEB_ROOT / "index.html"
        if not target.is_file():
            self._json(404, {"error": "web UI not found", "looked_in": str(WEB_ROOT)})
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        body: dict[str, Any] = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if raw:
                try:
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        body = {"value": body}
                except json.JSONDecodeError:
                    self._json(400, {"error": "request body is not valid JSON"})
                    return
        for m, pattern, fn in ROUTES:
            if m != method:
                continue
            match = pattern.match(parsed.path)
            if match:
                body["_path"] = list(match.groups())
                try:
                    self._json(200, fn(qs, body))
                except KeyError as exc:
                    self._json(400, {"error": f"missing required field {exc}"})
                except Exception as exc:  # noqa: BLE001 - surface, never crash the server
                    self._json(500, {"error": f"{type(exc).__name__}: {exc}",
                                     "traceback": traceback.format_exc()[-2000:]})
                return
        if method == "GET" and not parsed.path.startswith("/api/"):
            self._static(parsed.path)
            return
        self._json(404, {"error": f"no route for {method} {parsed.path}",
                         "routes": sorted({p.pattern for m, p, _ in ROUTES if m == method})})

    def do_GET(self) -> None:      # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:     # noqa: N802
        self._dispatch("POST")


def _json_default(o: Any) -> Any:
    import numpy as np
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o)
        return v if v == v and abs(v) != float("inf") else None
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "__dict__"):
        return {k: v for k, v in vars(o).items() if not k.startswith("_")}
    return str(o)


def create_server(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host or SETTINGS.host, port or SETTINGS.port), _Handler)


def serve(host: str | None = None, port: int | None = None) -> None:
    srv = create_server(host, port)
    h, p = srv.server_address[0], srv.server_address[1]
    print(f"AI Trading Brain -> http://{h}:{p}")
    print(f"  data path: {__import__('tradingbrain.data.providers.registry', fromlist=['HUB']).HUB.data_note()}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        srv.server_close()
