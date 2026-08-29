"""AI orchestration.

Two modes, one contract: **every number in an answer comes from a tool result.**

``router`` mode (always available, no API key, no network) parses the question,
picks tools, runs them and renders the answer from the returned structures. It
cannot hallucinate because it has no generative component.

``llm`` mode (when ANTHROPIC_API_KEY is set and the API is reachable) runs an
Anthropic tool-use loop over the SAME tool registry. The system prompt forbids
the model from producing figures of its own, and the transcript of tool calls is
returned alongside the answer so any claim can be traced to the call that
produced it.
"""

from __future__ import annotations

import dataclasses
import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable

from ..config import SETTINGS, Settings
from ..provenance import EVIDENCE_DESCRIPTIONS
from .tools import TOOLS, call_tool, tool_catalog

SYSTEM_PROMPT = """You are the AI Brain of a quantitative trading research system.

ABSOLUTE RULES
1. You must NEVER state a number, statistic, probability, price, backtest result
   or market observation that did not come back from a tool call in this
   conversation. If you need a figure, call a tool. If no tool provides it, say
   the system cannot currently determine it.
2. Label every claim with its evidence class: SOURCE_FACT, INFERENCE,
   HYPOTHESIS, BACKTEST_RESULT, LIVE_MARKET_OBSERVATION or MODEL_ESTIMATE.
   Never present one class as another. A source saying something is a
   SOURCE_FACT about the source, not a fact about markets.
3. Always show sample size with any proportion, and phrase it as "X% of the N
   qualifying historical events did Y", never as "X% chance".
4. If a tool result says the data origin is SYNTHETIC, say so prominently in
   your answer: the figures describe a generated market, not a real one.
5. Be skeptical. Ask what the sample size is, whether the result survives out of
   sample, whether it could be overfit, whether costs were included. Say
   "insufficient evidence" when that is the honest answer.
6. Do not validate the user's beliefs. If they say a stock will break out, check
   whether the setup actually qualifies and give the strongest case against.
7. You are a research and decision-support system, not a source of guarantees.
   Never say a trade will win.
"""


@dataclasses.dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        return {"tool": self.name, "arguments": self.arguments,
                "result_keys": sorted(self.result)[:24],
                "error": self.result.get("error")}


@dataclasses.dataclass
class Answer:
    mode: str
    question: str
    blocks: list[dict[str, Any]]
    tool_calls: list[ToolCall]
    claims: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "question": self.question, "blocks": self.blocks,
                "tool_calls": [t.summary() for t in self.tool_calls],
                "tool_results": {t.name: t.result for t in self.tool_calls},
                "claims": self.claims, "warnings": self.warnings,
                "evidence_classes": dict(EVIDENCE_DESCRIPTIONS)}

    def text(self) -> str:
        out = []
        for b in self.blocks:
            if b.get("heading"):
                out.append(f"\n{b['heading']}\n" + "-" * len(b["heading"]))
            for line in b.get("lines", []):
                out.append(line)
        return "\n".join(out)


# ---------------------------------------------------------------------------
# deterministic router
# ---------------------------------------------------------------------------

#: Tickers are matched only where they appear in UPPERCASE in the original
#: question. Lower-casing first made "are", "all" and "can" look like symbols.
TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")
STOPWORDS = {"AI", "I", "A", "THE", "IS", "IT", "DO", "MY", "ME", "AND", "OR", "NOT",
             "WHY", "HOW", "WHAT", "SHOULD", "SPY", "QQQ", "R", "SMA", "EMA", "ATR",
             "ADR", "IV", "US", "OK", "NO", "YES", "BUY", "SELL", "PM", "AM", "ARE",
             "ALL", "CAN", "BE", "IN", "ON", "AT", "OF", "TO", "IF", "SO", "AN"}


def _extract_symbol(q: str) -> str | None:
    from ..data.providers.registry import HUB
    universe = {s.upper() for s in HUB.universe()}
    caps = [m for m in TICKER_RE.findall(q) if m not in STOPWORDS]
    for m in caps:
        if m in universe and m not in ("SPY", "QQQ"):
            return m
    for m in caps:
        if m in universe:
            return m
    return caps[0] if caps else None


class Router:
    """Maps a question onto a plan of tool calls and renders the result."""

    def __init__(self) -> None:
        self.rules: list[tuple[re.Pattern[str], Callable[[str, str | None], list[tuple[str, dict]]],
                               str]] = [
            (re.compile(r"\b(mistakes?|my trading|my trades|how am i|have i been|performance|"
                        r"journal|overtrad|cut winners|let losers)\b", re.I),
             lambda q, s: [("analyze_trading_performance", {})], "journal"),
            (re.compile(r"\b(best setups?|top setups?|scan|today'?s|screener?|candidates)\b", re.I),
             lambda q, s: [("rank_stocks", {"limit": 15}),
                           ("detect_market_regime", {"with_breadth": True})], "scan"),
            (re.compile(r"\b(regime|market condition|is the market|bull|bear|breadth)\b", re.I),
             lambda q, s: [("detect_market_regime", {"with_breadth": True})], "regime"),
            (re.compile(r"\b(sectors?|groups?)\b", re.I),
             lambda q, s: [("rank_sectors", {}), ("rank_themes", {})], "sectors"),
            (re.compile(r"\b(themes?)\b", re.I),
             lambda q, s: [("rank_themes", {})], "sectors"),
            (re.compile(r"\b(option|call|put|strike|contract|expir)\b", re.I),
             lambda q, s: ([("analyze_options", {"symbol": s})] if s else
                           [("system_status", {})]), "options"),
            (re.compile(r"\b(backtest|test this|expectancy of|profit factor)\b", re.I),
             lambda q, s: [("run_backtest", {"strategy_key": _strategy_from(q)})], "backtest"),
            (re.compile(r"\b(walk.?forward|out of sample|out-of-sample)\b", re.I),
             lambda q, s: [("run_walk_forward_test", {})], "walkforward"),
            (re.compile(r"\b(monte ?carlo|ruin|drawdown distribution)\b", re.I),
             lambda q, s: [("run_monte_carlo", {})], "montecarlo"),
            (re.compile(r"\b(overfit|sensitiv|robust|parameter)\b", re.I),
             lambda q, s: [("run_sensitivity_analysis", {})], "sensitivity"),
            (re.compile(r"\b(does|do|is|are|which|compare|better|improve)\b.*"
                        r"\b(volume|sector|relative strength|extended|market filter|"
                        r"10 ?sma|20 ?sma|trail|consolidat|prior move)\b", re.I),
             lambda q, s: [("run_hypothesis_test", {"question": q})], "hypothesis"),
            (re.compile(r"\b(similar|historical example|analog|历史)\b", re.I),
             lambda q, s: [("find_historical_setups", {})], "analogs"),
            (re.compile(r"\b(where did|why does the ai|source|kullam|morgan|sar says|"
                        r"knowledge base)\b", re.I),
             lambda q, s: [("search_strategy_knowledge", {"query": _kb_query(q)})], "knowledge"),
            (re.compile(r"\b(position size|how many shares|risk)\b", re.I),
             lambda q, s: [("system_status", {})], "risk"),
            (re.compile(r"\b(analyz|analys|should i|good trade|qualif|break ?out|"
                        r"why not|bear case)\b", re.I),
             lambda q, s: ([("analyze_trade", {"symbol": s})] if s else
                           [("rank_stocks", {"limit": 10})]), "analyze"),
        ]

    def plan(self, question: str) -> tuple[list[tuple[str, dict]], str]:
        sym = _extract_symbol(question)
        for pattern, builder, kind in self.rules:
            if pattern.search(question):
                return builder(question, sym), kind
        if sym:
            return [("analyze_trade", {"symbol": sym})], "analyze"
        return [("system_status", {})], "status"


def _strategy_from(q: str) -> str:
    ql = q.lower()
    if "kullam" in ql:
        return "kullamagi_breakout_v1_0"
    if "20" in ql and "trail" in ql:
        return "sar_trail20_v1_0"
    if "no filter" in ql or "without" in ql:
        return "sar_no_market_filter_v1_0"
    if "episodic" in ql or "pivot" in ql:
        return "episodic_pivot_v0_1"
    return "sar_v1_1_adr_stop"


def _kb_query(q: str) -> str:
    for word in ("volume", "consolidation", "breakout", "stop", "trail", "partial",
                 "market", "relative strength", "extended", "prior", "episodic",
                 "moving average", "risk"):
        if word in q.lower():
            return word
    return ""


# ---------------------------------------------------------------------------
# rendering (router mode)
# ---------------------------------------------------------------------------

def _r(v: Any, nd: int = 1) -> Any:
    """Round for display. The full-precision value stays in tool_results."""
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return v


def _render(kind: str, calls: list[ToolCall], question: str) -> tuple[list[dict], list[dict], list[str]]:
    blocks: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    warnings: list[str] = []
    by_name = {c.name: c.result for c in calls}

    for c in calls:
        if c.result.get("error"):
            warnings.append(f"{c.name}: {c.result['error']}")
        origin = c.result.get("data_origin") or (c.result.get("meta") or {}).get("data_origin")
        if origin == "SYNTHETIC":
            warnings.append("Data origin is SYNTHETIC: every figure below describes a "
                            "generated market, not a real one.")
        for key in ("claim",):
            if isinstance(c.result.get(key), dict):
                claims.append(c.result[key])
        for cl in (c.result.get("claims") or []):
            if isinstance(cl, dict):
                claims.append(cl)

    if kind == "regime" and "detect_market_regime" in by_name:
        r = by_name["detect_market_regime"]
        lines = [f"Regime: {r.get('label')}   Volatility: {r.get('volatility_label')}   "
                 f"Strength: {_r(r.get('strength'))}/100",
                 f"Index MA filter: {'PASSES' if r.get('filter_pass') else 'FAILS'} -- "
                 f"{r.get('filter_detail')}"]
        b = r.get("breadth") or {}
        if b.get("available"):
            lines.append(f"Breadth: {b.get('pct_above_50sma')}% above the 50 SMA, "
                         f"{b.get('pct_above_200sma')}% above the 200 SMA "
                         f"(N={b.get('symbols_counted')} symbols).")
        lines.append("A regime label describes the recent past. It is not a forecast.")
        blocks.append({"heading": "MARKET", "lines": lines})

    if kind == "scan" and "rank_stocks" in by_name:
        r = by_name["rank_stocks"]
        lines = [f"{r.get('candidates_found')} candidates from {r.get('symbols_scanned')} "
                 f"symbols on {r.get('as_of')} ({r.get('breakouts_today')} triggered today).",
                 "Score is a definition-match score, not a probability or an expected return."]
        for c2 in (r.get("results") or [])[:15]:
            lines.append(f"  {c2['symbol']:<6} {c2['score']:>5.1f}  {c2['state']:<11} "
                         f"sector={c2.get('sector')}  to-breakout={c2.get('pct_to_breakout')}%  "
                         f"stop-risk={c2.get('risk_pct')}%")
        blocks.append({"heading": "TOP SETUPS", "lines": lines})
        if r.get("leading_sectors"):
            blocks.append({"heading": "LEADING SECTORS",
                           "lines": [f"  {i+1}. {g['name']} ({g['score']})"
                                     for i, g in enumerate(r["leading_sectors"])]})

    if kind == "sectors":
        for key, title in (("rank_sectors", "SECTORS"), ("rank_themes", "THEMES")):
            if key in by_name:
                lines = [f"  {g['rank']:>2}. {g['name']:<26} score {_r(g['score'])} "
                         f"(3m median {_r(g['components'].get('return_3m'))}%, "
                         f"{_r(g['components'].get('pct_above_50sma'))}% above 50 SMA)"
                         for g in (by_name[key].get("ranks") or [])[:10]]
                lines.append("Weights are configurable and unvalidated; component values are "
                             "shown so the ranking can be argued with.")
                blocks.append({"heading": title, "lines": lines})

    if kind == "analyze" and "analyze_trade" in by_name:
        a = by_name["analyze_trade"]
        if not a.get("ok"):
            blocks.append({"heading": "ANALYSIS", "lines": [a.get("reason", "failed")]})
        else:
            v = a["verdict"]
            blocks.append({"heading": f"VERDICT: {v['label']} ({v['score']}/100)",
                           "lines": [v["headline"], ""] + [f"  {r}" for r in v["reasons"]]})
            blocks.append({"heading": "SETUP",
                           "lines": [f"State: {a['setup']['state']}",
                                     f"Matches strategy: {a['setup']['matches_strategy']}"]
                           + [f"  failed: {x}" for x in a["setup"]["failed_checks"][:8]]})
            blocks.append({"heading": "MARKET / SECTOR / STOCK", "lines": [
                f"Regime {a['market']['regime']} ({a['market']['volatility']}), filter "
                f"{'passes' if a['market']['index_filter_passes'] else 'FAILS'}",
                f"Sector {a['sector']['name']} ranked {a['sector']['rank']} of {a['sector']['of']}",
                f"Relative strength percentile: {a['stock']['relative_strength_percentile']}",
                f"Extension above 20 SMA: {a['stock']['extension'].get('pct_above_ma')}% "
                f"({a['stock']['extension'].get('adr_above_ma')} ADRs)"]})
            an = a.get("historical_analogs") or {}
            if an.get("run"):
                lines = [f"N = {an.get('n')} comparable historical events. "
                         f"{an.get('sample_verdict')}"]
                for h, hv in (an.get("horizons") or {}).items():
                    lines.append(f"  {h:>2}d: {hv['statement']} 95% CI {hv['pct_positive_ci']}")
                if an.get("path"):
                    lines.append("  " + an["path"]["statement"])
                blocks.append({"heading": "HISTORICAL EVIDENCE", "lines": lines})
            blocks.append({"heading": "BEAR CASE",
                           "lines": [f"  - {x}" for x in a["bear_case"]]})
            blocks.append({"heading": "INVALIDATION",
                           "lines": [f"  - {x}" for x in a["invalidation"]]})
            plan = (a.get("risk") or {}).get("plan") or {}
            if plan.get("ok"):
                blocks.append({"heading": "RISK", "lines": [
                    f"Entry {a['risk']['entry']}  Stop {a['risk']['stop']}  "
                    f"({plan['risk_per_share_pct']:.2f}% per share)",
                    f"Size {plan['shares']} shares = {plan['position_pct_of_equity']:.1f}% of "
                    f"equity, risking ${plan['actual_risk_dollars']:.2f} "
                    f"({plan['actual_risk_pct']:.2f}%)"]})
            blocks.append({"heading": "EVIDENCE QUALITY",
                           "lines": [a["evidence_quality"]["grade"]]
                           + [f"  {n}" for n in a["evidence_quality"]["notes"]]})

    if kind == "hypothesis" and "run_hypothesis_test" in by_name:
        r = by_name["run_hypothesis_test"]
        lines = [f"Experiment #{r.get('experiment_id')} ({r.get('kind')})",
                 f"Verdict: {r.get('verdict')}", "", r.get("conclusion", "")]
        for g in (r.get("groups") or []):
            lines.append(f"  {g['label']}: N={g['n']}, mean "
                         f"{g['stats'].get('mean')}%, positive {g.get('pct_positive')}% "
                         f"CI {g.get('pct_positive_ci')}")
        for row in (r.get("ranked") or [])[:8]:
            lines.append(f"  {row['label']:<40} {row['expectancy_r']}R over "
                         f"{row['n_trades']} trades  CI {row.get('expectancy_ci')}")
        blocks.append({"heading": "HYPOTHESIS TEST", "lines": lines})

    if kind == "backtest" and "run_backtest" in by_name:
        r = by_name["run_backtest"]
        m = r.get("metrics", {})
        blocks.append({"heading": "BACKTEST", "lines": [
            f"{r.get('strategy')}  {r.get('start')} .. {r.get('end')}  "
            f"({r.get('n_symbols')} symbols)",
            f"Trades {m.get('n_trades')}  Win rate {m.get('win_rate_pct')}%  "
            f"Expectancy {m.get('expectancy_r')}R  Profit factor {m.get('profit_factor')}",
            f"Total return {m.get('total_return_pct')}%  CAGR {m.get('cagr_pct')}  "
            f"Max DD {m.get('max_drawdown_pct')}%  Sharpe {m.get('sharpe')}",
            "", "Rejection breakdown (why candidates were filtered out):"]
            + [f"  {k}: {v}" for k, v in list((r.get("signals_rejected") or {}).items())[:8]]
            + [""] + [f"  ! {w}" for w in (r.get("warnings") or [])[:6]]})

    if kind == "montecarlo" and "run_monte_carlo" in by_name:
        r = by_name["run_monte_carlo"]
        if r.get("ok"):
            blocks.append({"heading": "MONTE CARLO", "lines": [
                f"Median total return {r['total_return_pct']['p50']:.1f}%  "
                f"(5th pct {r['total_return_pct']['p05']:.1f}%, "
                f"95th {r['total_return_pct']['p95']:.1f}%)",
                f"Median worst drawdown {r['max_drawdown_pct']['p50']:.1f}%  "
                f"(worst simulated {r['max_drawdown_pct']['worst']:.1f}%)",
                f"P(end below start) {r['prob_decline_pct']:.1f}%  "
                f"P(drawdown worse than 20%) {r['prob_drawdown_worse_than_20pct']:.1f}%",
                f"Losing streak: median {r['losing_streak']['p50']:.0f}, "
                f"99th pct {r['losing_streak']['p99']:.0f}"]
                + [""] + [f"  {a}" for a in r["assumptions"]]})
        else:
            blocks.append({"heading": "MONTE CARLO", "lines": [r.get("reason", "unavailable")]})

    if kind == "journal" and "analyze_trading_performance" in by_name:
        r = by_name["analyze_trading_performance"]
        s = r["summary"]
        lines = [f"{s['closed']} closed trades, {s['open']} open. Net "
                 f"{s['net_pnl']}, expectancy {s.get('expectancy_r')}R."]
        for f in r["findings"]:
            lines.append(f"  [{f.get('confidence')}] {f['finding']}")
            lines.append(f"      {f.get('detail','')}")
        blocks.append({"heading": "YOUR TRADING", "lines": lines})

    if kind == "knowledge" and "search_strategy_knowledge" in by_name:
        r = by_name["search_strategy_knowledge"]
        lines = []
        for k in (r.get("results") or [])[:10]:
            flag = "" if k.get("fulltext_verified") else "  [UNVERIFIED SOURCE]"
            lines.append(f"  {k['category']} / {k['concept']}{flag}")
            lines.append(f"      source: {k.get('source')} {k.get('url') or ''}")
            lines.append(f"      rule:   {k.get('rule_text')}")
        lines.append(r["stats"]["verification_note"])
        blocks.append({"heading": "KNOWLEDGE BASE", "lines": lines})

    if kind == "options" and "analyze_options" in by_name:
        r = by_name["analyze_options"]
        lines = [f"Scenario: {r['scenario']['move_pct']}% move over "
                 f"{r['scenario']['hold_days']} days.",
                 r["source"]["detail"]]
        for row in r["rows"]:
            if row.get("ok"):
                lines.append(f"  {row['strike']} {row['type']} {row['expiration']} "
                             f"({row['dte']}d): premium {row['entry_premium']}, delta "
                             f"{row['delta']}, BE move {row['break_even_move_pct']}%")
        lines += [f"  {n}" for n in r["interpretation"]]
        blocks.append({"heading": "OPTIONS", "lines": lines})

    if not blocks:
        for c in calls:
            blocks.append({"heading": c.name.upper().replace("_", " "),
                           "lines": [json.dumps(c.result, default=str)[:2500]]})
    return blocks, claims, sorted(set(warnings))


# ---------------------------------------------------------------------------

class Orchestrator:
    def __init__(self, settings: Settings = SETTINGS) -> None:
        self.settings = settings
        self.router = Router()

    def available_modes(self) -> dict[str, Any]:
        return {"router": True, "llm": bool(self.settings.llm_enabled),
                "llm_reason": ("ANTHROPIC_API_KEY not set" if not self.settings.anthropic_api_key
                               else "enabled"),
                "model": self.settings.anthropic_model}

    def ask(self, question: str, mode: str | None = None,
            context: dict[str, Any] | None = None) -> Answer:
        mode = mode or ("llm" if self.settings.llm_enabled else "router")
        if mode == "llm":
            try:
                return self._ask_llm(question, context)
            except Exception as exc:  # noqa: BLE001
                ans = self._ask_router(question)
                ans.warnings.insert(0, f"LLM mode failed ({type(exc).__name__}: {exc}); "
                                        "answered with the deterministic router instead.")
                ans.mode = "router (llm fallback)"
                return ans
        return self._ask_router(question)

    # -- router ------------------------------------------------------------
    def _ask_router(self, question: str) -> Answer:
        plan, kind = self.router.plan(question)
        calls = [ToolCall(name, args, call_tool(name, **args)) for name, args in plan]
        blocks, claims, warnings = _render(kind, calls, question)
        blocks.insert(0, {"heading": None,
                          "lines": [f"[router mode] intent: {kind}; tools called: "
                                    + ", ".join(n for n, _ in plan),
                                    "Every figure below came from a tool result. Nothing was "
                                    "generated by a language model."]})
        return Answer("router", question, blocks, calls, claims, warnings)

    # -- llm ---------------------------------------------------------------
    def _anthropic_tools(self) -> list[dict[str, Any]]:
        tools = []
        for t in tool_catalog():
            props: dict[str, Any] = {}
            required: list[str] = []
            for p in t["parameters"]:
                ann = (p["annotation"] or "").lower()
                if "int" in ann and "float" not in ann:
                    js: dict[str, Any] = {"type": "integer"}
                elif "float" in ann:
                    js = {"type": "number"}
                elif "bool" in ann:
                    js = {"type": "boolean"}
                elif "list" in ann:
                    js = {"type": "array", "items": {}}
                elif "dict" in ann:
                    js = {"type": "object"}
                else:
                    js = {"type": "string"}
                props[p["name"]] = js
                if p["required"]:
                    required.append(p["name"])
            tools.append({"name": t["name"], "description": t["doc"] or t["name"],
                          "input_schema": {"type": "object", "properties": props,
                                           "required": required}})
        return tools

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json",
                     "x-api-key": self.settings.anthropic_api_key or "",
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    def _ask_llm(self, question: str, context: dict[str, Any] | None,
                 max_turns: int = 8) -> Answer:
        if not self.settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        if context:
            messages[0]["content"] = (f"{question}\n\nContext supplied by the application:\n"
                                      f"{json.dumps(context, default=str)[:4000]}")
        tools = self._anthropic_tools()
        calls: list[ToolCall] = []
        text_out: list[str] = []

        for _ in range(max_turns):
            payload = {"model": self.settings.anthropic_model, "max_tokens": 4096,
                       "system": SYSTEM_PROMPT, "tools": tools, "messages": messages}
            resp = self._post(payload)
            content = resp.get("content", [])
            messages.append({"role": "assistant", "content": content})
            tool_uses = [c for c in content if c.get("type") == "tool_use"]
            for c in content:
                if c.get("type") == "text" and c.get("text"):
                    text_out.append(c["text"])
            if not tool_uses:
                break
            results = []
            for tu in tool_uses:
                result = call_tool(tu["name"], **(tu.get("input") or {}))
                calls.append(ToolCall(tu["name"], tu.get("input") or {}, result))
                results.append({"type": "tool_result", "tool_use_id": tu["id"],
                                "content": json.dumps(result, default=str)[:60_000]})
            messages.append({"role": "user", "content": results})

        warnings = []
        for c in calls:
            origin = c.result.get("data_origin") or (c.result.get("meta") or {}).get("data_origin")
            if origin == "SYNTHETIC":
                warnings.append("Data origin is SYNTHETIC: figures describe a generated market.")
        used = ", ".join(dict.fromkeys(c.name for c in calls)) or "none"
        blocks = [
            {"heading": None,
             "lines": ["[llm mode] every figure below was produced by a tool call; the "
                       "model only framed them.", f"tools used: {used}"]},
            {"heading": None, "lines": "\n".join(text_out).split("\n")},
        ]
        claims = [c.result["claim"] for c in calls if isinstance(c.result.get("claim"), dict)]
        return Answer("llm", question, blocks, calls, claims, sorted(set(warnings)))


ORCHESTRATOR = Orchestrator()


def ask(question: str, mode: str | None = None,
        context: dict[str, Any] | None = None) -> dict[str, Any]:
    return ORCHESTRATOR.ask(question, mode, context).to_dict()
