"""Trade-journal analytics -- the personal half of the trading brain.

Behavioural claims are only made when the journal actually supports them. Every
finding carries the sample it was computed from, and a finding computed from
fewer than eight trades is reported as an observation, never as a pattern.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import statistics
from typing import Any, Iterable, Sequence

import numpy as np

from ..db.store import STORE, Store
from ..provenance import Claim, DataOrigin, EvidenceClass
from ..stats.core import bootstrap_ci, permutation_test, sample_size_verdict, wilson_interval

MIN_GROUP = 8


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _closed(trades: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in trades if _num(t.get("pnl")) is not None and t.get("exit_date")]


def journal_summary(store: Store = STORE) -> dict[str, Any]:
    trades = store.trades(limit=10_000)
    closed = _closed(trades)
    pnl = np.array([_num(t["pnl"]) for t in closed], float) if closed else np.array([])
    rs = np.array([_num(t.get("r_multiple")) for t in closed
                   if _num(t.get("r_multiple")) is not None], float)
    open_trades = [t for t in trades if not t.get("exit_date")]
    sample = [t for t in trades if "SAMPLE" in (t.get("tags") or "")]
    return {
        "sample_trades": len(sample),
        "sample_warning": (
            f"{len(sample)} of {len(trades)} logged trades are tagged SAMPLE: they were "
            "produced by the backtester, not taken in a real account. Behavioural findings "
            "computed over a mix of real and simulated trades are meaningless -- delete them "
            "(python -m tradingbrain.cli demo-journal --clear, or the x button per row) "
            "before logging real trades." if sample else None),
        "total_logged": len(trades),
        "closed": len(closed),
        "open": len(open_trades),
        "net_pnl": float(pnl.sum()) if len(pnl) else 0.0,
        "win_rate_pct": float((pnl > 0).mean() * 100) if len(pnl) else None,
        "expectancy_dollars": float(pnl.mean()) if len(pnl) else None,
        "expectancy_r": float(rs.mean()) if len(rs) else None,
        "first_entry": min((t["entry_date"] for t in trades if t.get("entry_date")), default=None),
        "last_entry": max((t["entry_date"] for t in trades if t.get("entry_date")), default=None),
    }


def _group_stats(trades: Sequence[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        g = t.get(key) or "(unspecified)"
        groups.setdefault(str(g), []).append(t)
    out = []
    for name, rows in groups.items():
        pnl = np.array([_num(r["pnl"]) for r in rows], float)
        rr = np.array([_num(r.get("r_multiple")) for r in rows
                       if _num(r.get("r_multiple")) is not None], float)
        wins = int((pnl > 0).sum())
        ci = wilson_interval(wins, len(pnl))
        out.append({
            "group": name, "n": len(rows),
            "net_pnl": round(float(pnl.sum()), 2),
            "win_rate_pct": round(float(wins / len(pnl) * 100), 1) if len(pnl) else None,
            "win_rate_ci_pct": [round(ci.ci_low * 100, 1), round(ci.ci_high * 100, 1)]
            if ci.ci_low is not None else None,
            "expectancy_dollars": round(float(pnl.mean()), 2) if len(pnl) else None,
            "expectancy_r": round(float(rr.mean()), 3) if len(rr) else None,
            "sample_verdict": sample_size_verdict(len(rows)),
            "reportable": len(rows) >= MIN_GROUP,
        })
    out.sort(key=lambda d: (-(d["expectancy_r"] if d["expectancy_r"] is not None else -99),
                            -d["n"]))
    return out


def analyze_trading_performance(store: Store = STORE,
                                min_group: int = MIN_GROUP) -> dict[str, Any]:
    trades = store.trades(limit=10_000)
    closed = _closed(trades)
    summary = journal_summary(store)
    out: dict[str, Any] = {"summary": summary, "findings": [], "by": {}, "claims": []}

    if len(closed) < 5:
        out["findings"].append({
            "finding": "Not enough closed trades to analyse.",
            "detail": f"{len(closed)} closed trade(s) logged. Behavioural analysis needs "
                      "dozens of trades before any pattern is distinguishable from noise.",
            "n": len(closed), "confidence": "NONE"})
        return out

    for key, label in (("setup", "setup"), ("strategy", "strategy"),
                       ("market_regime", "market regime"), ("sector", "sector"),
                       ("direction", "direction"), ("instrument", "instrument")):
        out["by"][key] = _group_stats(closed, key)

    rs = np.array([_num(t.get("r_multiple")) for t in closed
                   if _num(t.get("r_multiple")) is not None], float)
    winners = [t for t in closed if (_num(t["pnl"]) or 0) > 0]
    losers = [t for t in closed if (_num(t["pnl"]) or 0) < 0]

    # --- do I cut winners early / let losers run? -------------------------
    hw = [_hold_days(t) for t in winners]
    hl = [_hold_days(t) for t in losers]
    hw = [h for h in hw if h is not None]
    hl = [h for h in hl if h is not None]
    if len(hw) >= min_group and len(hl) >= min_group:
        test = permutation_test(hw, hl, np.mean, n_perm=5000, name="winner vs loser hold time")
        mw, ml = statistics.mean(hw), statistics.mean(hl)
        if mw < ml and test.p_value is not None and test.p_value < 0.10:
            out["findings"].append({
                "finding": "Winners are being held for less time than losers.",
                "detail": (f"Median hold: winners {statistics.median(hw):.1f} days "
                           f"(n={len(hw)}), losers {statistics.median(hl):.1f} days "
                           f"(n={len(hl)}). Permutation p={test.p_value:.3f}. This is the "
                           "signature of cutting winners early and letting losers run."),
                "n": len(hw) + len(hl), "p_value": test.p_value,
                "confidence": "SUPPORTED" if test.p_value < 0.05 else "SUGGESTIVE"})
        else:
            out["findings"].append({
                "finding": "No evidence of cutting winners early.",
                "detail": (f"Mean hold: winners {mw:.1f} days, losers {ml:.1f} days "
                           f"(permutation p={test.p_value:.3f})."),
                "n": len(hw) + len(hl), "p_value": test.p_value, "confidence": "TESTED"})
    else:
        out["findings"].append({
            "finding": "Hold-time asymmetry not testable yet.",
            "detail": f"Needs at least {min_group} winners and {min_group} losers with "
                      f"dated entries and exits; have {len(hw)} and {len(hl)}.",
            "n": len(hw) + len(hl), "confidence": "INSUFFICIENT_DATA"})

    # --- position-sizing discipline ---------------------------------------
    breaches = []
    for t in closed:
        eq = _num(t.get("account_equity_at_entry"))
        entry, stop, size = (_num(t.get("entry_price")), _num(t.get("stop_price")),
                             _num(t.get("position_size")))
        if eq and entry and stop and size and eq > 0:
            risk_pct = abs(entry - stop) * size / eq * 100.0
            if risk_pct > 2.0:
                breaches.append({"id": t["id"], "ticker": t["ticker"],
                                 "risk_pct": round(risk_pct, 2),
                                 "date": t.get("entry_date")})
    sizeable = [t for t in closed if _num(t.get("account_equity_at_entry"))
                and _num(t.get("stop_price")) and _num(t.get("position_size"))]
    if sizeable:
        out["findings"].append({
            "finding": (f"{len(breaches)} of {len(sizeable)} sized trades risked more than "
                        "2% of stated equity."),
            "detail": ("Computed as |entry-stop| x size / equity at entry. Trades without "
                       "equity, stop and size recorded are excluded."),
            "n": len(sizeable), "examples": breaches[:10],
            "confidence": "MEASURED" if len(sizeable) >= min_group else "OBSERVATION"})
    else:
        out["findings"].append({
            "finding": "Position-sizing discipline cannot be checked.",
            "detail": "No closed trade records entry, stop, size AND account equity at entry. "
                      "Log all four to enable this check.",
            "n": 0, "confidence": "INSUFFICIENT_DATA"})

    # --- overtrading -------------------------------------------------------
    by_day: dict[str, int] = {}
    for t in closed:
        d = (t.get("entry_date") or "")[:10]
        if d:
            by_day[d] = by_day.get(d, 0) + 1
    if by_day:
        counts = np.array(list(by_day.values()), float)
        busy = {d: c for d, c in by_day.items() if c >= 4}
        if busy and len(closed) >= 20:
            busy_r = [_num(t.get("r_multiple")) for t in closed
                      if (t.get("entry_date") or "")[:10] in busy]
            quiet_r = [_num(t.get("r_multiple")) for t in closed
                       if (t.get("entry_date") or "")[:10] not in busy]
            busy_r = [r for r in busy_r if r is not None]
            quiet_r = [r for r in quiet_r if r is not None]
            if len(busy_r) >= min_group and len(quiet_r) >= min_group:
                test = permutation_test(busy_r, quiet_r, np.mean, n_perm=5000,
                                        name="busy-day vs quiet-day R")
                out["findings"].append({
                    "finding": ("Results on high-activity days differ from quiet days."
                                if test.p_value and test.p_value < 0.10 else
                                "No detectable overtrading effect."),
                    "detail": (f"Mean R on days with >=4 entries: "
                               f"{statistics.mean(busy_r):.3f} (n={len(busy_r)}); other days "
                               f"{statistics.mean(quiet_r):.3f} (n={len(quiet_r)}); "
                               f"permutation p={test.p_value:.3f}."),
                    "n": len(busy_r) + len(quiet_r), "p_value": test.p_value,
                    "confidence": "TESTED"})
        out["by"]["activity"] = {
            "trading_days": len(by_day),
            "mean_entries_per_active_day": round(float(counts.mean()), 2),
            "max_entries_in_a_day": int(counts.max()),
            "days_with_4_or_more": len([c for c in counts if c >= 4]),
        }

    # --- chasing: entering far above the 20 SMA ---------------------------
    chase = [t for t in closed if isinstance(t.get("notes"), str)
             and "chase" in (t.get("notes") or "").lower()]
    if chase:
        out["findings"].append({
            "finding": f"{len(chase)} trades are self-tagged as chasing in the notes.",
            "detail": "Self-tagged only. A price-based chase test requires the entry price "
                      "and date on every trade plus market data for that symbol.",
            "n": len(chase), "confidence": "OBSERVATION"})

    # --- best / worst buckets ---------------------------------------------
    for key in ("setup", "market_regime", "strategy"):
        rows = [g for g in out["by"].get(key, []) if g["reportable"]]
        if len(rows) >= 2:
            best, worst = rows[0], rows[-1]
            out["findings"].append({
                "finding": f"Best and worst {key} by expectancy.",
                "detail": (f"Best: {best['group']} at {best['expectancy_r']}R over "
                           f"{best['n']} trades. Worst: {worst['group']} at "
                           f"{worst['expectancy_r']}R over {worst['n']} trades. "
                           "Both samples are small; treat as descriptive."),
                "n": best["n"] + worst["n"], "confidence": "DESCRIPTIVE"})

    if len(rs) >= 10:
        ci = bootstrap_ci(rs, np.mean, n_boot=4000, name="expectancy (R)")
        out["claims"].append(Claim(
            statement=(f"Journal expectancy is {ci.estimate:.3f}R over {ci.n} closed trades "
                       f"with a 95% bootstrap interval of "
                       f"[{ci.ci_low:.3f}, {ci.ci_high:.3f}]R."),
            evidence=EvidenceClass.INFERENCE,
            value=ci.estimate, sample_size=ci.n,
            confidence_interval=(ci.ci_low, ci.ci_high),
            data_source="operator trade journal", data_origin=DataOrigin.REAL,
            methodology="Percentile bootstrap over logged R-multiples.",
            caveats=["Self-reported journal data. Missing or mis-logged trades bias this.",
                     "Trades are not independent: they cluster in time and regime."],
        ).to_dict())

    out["disclaimer"] = ("Every statement above is computed from the journal only. Where the "
                         "journal lacks a field, the check is reported as not testable rather "
                         "than guessed.")
    return out


def _hold_days(t: dict[str, Any]) -> float | None:
    try:
        a = dt.date.fromisoformat((t.get("entry_date") or "")[:10])
        b = dt.date.fromisoformat((t.get("exit_date") or "")[:10])
        return float((b - a).days)
    except (ValueError, TypeError):
        return None


CSV_FIELDS = ("ticker", "direction", "strategy", "setup", "entry_date", "entry_price",
              "stop_price", "target_price", "position_size", "exit_date", "exit_price",
              "pnl", "r_multiple", "fees", "instrument", "thesis", "market_regime",
              "sector", "reason_entry", "reason_exit", "mistakes", "notes", "tags",
              "account_equity_at_entry", "screenshot_path")


def import_trades_csv(text: str, store: Store = STORE) -> dict[str, Any]:
    """Import a journal CSV. Unknown columns are ignored; bad rows are reported."""
    reader = csv.DictReader(io.StringIO(text))
    imported, errors = 0, []
    for n, row in enumerate(reader, 2):
        data = {k: (v.strip() if isinstance(v, str) else v)
                for k, v in row.items() if k in CSV_FIELDS and v not in (None, "")}
        if not data.get("ticker"):
            errors.append(f"line {n}: no ticker")
            continue
        for f in ("entry_price", "stop_price", "target_price", "position_size",
                  "exit_price", "pnl", "r_multiple", "fees", "account_equity_at_entry"):
            if f in data:
                v = _num(data[f])
                if v is None:
                    errors.append(f"line {n}: {f}={data[f]!r} is not a number")
                    data.pop(f)
                else:
                    data[f] = v
        if "pnl" not in data and all(k in data for k in ("entry_price", "exit_price",
                                                         "position_size")):
            sign = -1.0 if data.get("direction") == "short" else 1.0
            data["pnl"] = sign * (data["exit_price"] - data["entry_price"]) * data["position_size"]
        if "r_multiple" not in data and "pnl" in data and \
                all(k in data for k in ("entry_price", "stop_price", "position_size")):
            risk = abs(data["entry_price"] - data["stop_price"]) * data["position_size"]
            if risk > 0:
                data["r_multiple"] = data["pnl"] / risk
        store.add_trade(**data)
        imported += 1
    return {"imported": imported, "errors": errors,
            "recognised_columns": [c for c in (reader.fieldnames or []) if c in CSV_FIELDS],
            "ignored_columns": [c for c in (reader.fieldnames or []) if c not in CSV_FIELDS]}
