"""Contract analysis, comparison and scenario modelling."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Iterable

from ..data.providers.registry import HUB, DataHub
from ..provenance import Claim, DataOrigin, EvidenceClass
from .chain import RISK_FREE, get_chain
from .pricing import black_scholes, break_even, greeks, implied_volatility


def _find(chain: dict[str, Any], strike: float, expiration: str,
          option_type: str) -> dict[str, Any] | None:
    best, gap = None, float("inf")
    for c in chain["contracts"]:
        if c["type"] != option_type or c["expiration"] != expiration:
            continue
        d = abs(c["strike"] - strike)
        if d < gap:
            best, gap = c, d
    return best


def analyze_contract(symbol: str, strike: float, expiration: str, option_type: str = "call",
                     hub: DataHub = HUB, as_of: dt.date | None = None,
                     risk_free: float = RISK_FREE,
                     contracts: int = 1) -> dict[str, Any]:
    chain = get_chain(symbol, expiration, hub, as_of, risk_free)
    c = _find(chain, strike, expiration, option_type)
    if c is None:
        return {"ok": False,
                "reason": f"no {option_type} at ~{strike} expiring {expiration} in the chain",
                "source": chain["source"]}
    spot = chain["underlying"]
    t = max(c["dte"], 0) / 365.0
    mid = c.get("mid") or c.get("last") or 0.0
    iv = c.get("implied_volatility")
    solved_iv = implied_volatility(mid, spot, c["strike"], t, risk_free, option_type) if mid else None
    sigma = iv if iv else (solved_iv or 0.35)
    g = greeks(spot, c["strike"], t, risk_free, sigma, option_type)
    theo = black_scholes(spot, c["strike"], t, risk_free, sigma, option_type)
    be = break_even(c["strike"], mid or theo, option_type)

    out = {
        "ok": True,
        "symbol": symbol.upper(), "type": option_type, "strike": c["strike"],
        "expiration": expiration, "dte": c["dte"], "underlying": spot,
        "as_of": chain["as_of"],
        "quote": {"bid": c.get("bid"), "ask": c.get("ask"), "mid": mid,
                  "last": c.get("last"), "spread_pct": c.get("spread_pct"),
                  "volume": c.get("volume"), "open_interest": c.get("open_interest")},
        "volatility": {"implied_volatility_pct": round(sigma * 100, 2),
                       "solved_from_mid_pct": round(solved_iv * 100, 2) if solved_iv else None,
                       "iv_rank": c.get("iv_rank"), "iv_percentile": c.get("iv_percentile")},
        "greeks": {k: round(v, 6) for k, v in g.items()},
        "theoretical_value": round(theo, 4),
        "break_even": round(be, 4),
        "break_even_move_pct": round((be / spot - 1) * 100, 2),
        "position": {
            "contracts": contracts,
            "premium_paid": round((mid or theo) * 100 * contracts, 2),
            "max_loss": round((mid or theo) * 100 * contracts, 2),
            "max_loss_note": "maximum loss for a long option is the premium paid",
            "notional_delta_shares": round(g["delta"] * 100 * contracts, 1),
            "daily_theta_dollars": round(g["theta"] * 100 * contracts, 2),
        },
        "source": chain["source"],
        "assumptions": chain.get("model_assumptions", []),
        "claim": Claim(
            statement=(f"{symbol.upper()} {c['strike']} {option_type} expiring {expiration} "
                       f"({c['dte']} DTE): theoretical value {theo:.2f}, delta "
                       f"{g['delta']:.3f}, theta {g['theta']:.3f}/day at "
                       f"{sigma*100:.1f}% volatility."),
            evidence=EvidenceClass.MODEL_ESTIMATE,
            value={"theoretical_value": theo, **g},
            data_source=chain["source"]["source"],
            data_origin=DataOrigin.SYNTHETIC if chain["source"]["is_model_estimate"]
            else DataOrigin.REAL,
            methodology="Black-Scholes with the stated volatility and risk-free rate.",
            caveats=(["Chain is MODEL-GENERATED: strikes, premiums and greeks are computed, "
                      "not quoted. Do not trade from them."]
                     if chain["source"]["is_model_estimate"] else
                     ["Greeks recomputed from the vendor's implied volatility; the vendor's "
                      "own greeks may differ."])
            + ["Black-Scholes assumes constant volatility and European exercise; US equity "
               "options are American."],
        ).to_dict(),
    }
    return out


def compare_contracts(symbol: str, specs: Iterable[dict[str, Any]], hub: DataHub = HUB,
                      as_of: dt.date | None = None, move_pct: float = 10.0,
                      hold_days: int = 10) -> dict[str, Any]:
    """Compare several contracts on the same underlying move.

    The comparison is what actually matters when choosing a strike/expiry: what
    each contract is worth after the SAME move over the SAME horizon, net of the
    theta paid to get there.
    """
    rows: list[dict[str, Any]] = []
    for s in specs:
        a = analyze_contract(symbol, float(s["strike"]), s["expiration"],
                             s.get("type", "call"), hub, as_of,
                             contracts=int(s.get("contracts", 1)))
        if not a.get("ok"):
            rows.append({"spec": s, "ok": False, "reason": a.get("reason")})
            continue
        spot = a["underlying"]
        sigma = a["volatility"]["implied_volatility_pct"] / 100.0
        t_now = a["dte"] / 365.0
        t_then = max(a["dte"] - hold_days, 0) / 365.0
        entry = a["quote"]["mid"] or a["theoretical_value"]
        target_spot = spot * (1 + move_pct / 100.0)
        then = black_scholes(target_spot, a["strike"], t_then, RISK_FREE, sigma, a["type"])
        flat = black_scholes(spot, a["strike"], t_then, RISK_FREE, sigma, a["type"])
        down = black_scholes(spot * (1 - move_pct / 100.0), a["strike"], t_then,
                             RISK_FREE, sigma, a["type"])
        rows.append({
            "spec": s, "ok": True, "strike": a["strike"], "type": a["type"],
            "expiration": a["expiration"], "dte": a["dte"],
            "entry_premium": round(entry, 3),
            "delta": a["greeks"]["delta"], "theta_per_day": a["greeks"]["theta"],
            "vega": a["greeks"]["vega"], "gamma": a["greeks"]["gamma"],
            "break_even": a["break_even"], "break_even_move_pct": a["break_even_move_pct"],
            "spread_pct": a["quote"]["spread_pct"],
            f"value_if_up_{move_pct:g}pct_in_{hold_days}d": round(then, 3),
            f"pnl_if_up_{move_pct:g}pct_pct": round((then / entry - 1) * 100, 1) if entry else None,
            f"pnl_if_flat_{hold_days}d_pct": round((flat / entry - 1) * 100, 1) if entry else None,
            f"pnl_if_down_{move_pct:g}pct_pct": round((down / entry - 1) * 100, 1) if entry else None,
            "capital_per_contract": round(entry * 100, 2),
        })
    ok_rows = [r for r in rows if r.get("ok")]
    best = max(ok_rows, key=lambda r: r.get(f"pnl_if_up_{move_pct:g}pct_pct") or -1e9) \
        if ok_rows else None
    return {
        "symbol": symbol.upper(), "scenario": {"move_pct": move_pct, "hold_days": hold_days},
        "rows": rows,
        "highest_percentage_gain_on_the_up_case": best["spec"] if best else None,
        "interpretation": [
            "These are model values under a SINGLE assumed move and an unchanged implied "
            "volatility. Real option P/L is dominated by what IV does, which this holds fixed.",
            "The highest percentage gain is usually the cheapest, furthest out-of-the-money "
            "contract -- which is also the one most likely to expire worthless. Read the "
            "flat and down columns before the up column.",
        ],
        "source": chain_source_for(symbol, hub, as_of),
    }


def chain_source_for(symbol: str, hub: DataHub = HUB,
                     as_of: dt.date | None = None) -> dict[str, Any]:
    from .chain import chain_source
    return chain_source()


def scenario_matrix(underlying: float, strike: float, dte: int, premium: float,
                    iv_pct: float, option_type: str = "call", contracts: int = 1,
                    prices: list[float] | None = None,
                    days_forward: list[int] | None = None,
                    iv_shifts_pct: list[float] | None = None,
                    risk_free: float = RISK_FREE) -> dict[str, Any]:
    """Value the contract across a grid of underlying prices, dates and IV shifts."""
    sigma = iv_pct / 100.0
    prices = prices or [round(underlying * (1 + p / 100.0), 2)
                        for p in (-20, -10, -5, 0, 5, 10, 20, 30)]
    days_forward = days_forward or [0, max(dte // 4, 1), max(dte // 2, 1), dte]
    iv_shifts_pct = iv_shifts_pct if iv_shifts_pct is not None else [-25.0, 0.0, 25.0]

    grid = []
    for shift in iv_shifts_pct:
        sig = max(sigma * (1 + shift / 100.0), 0.01)
        block = {"iv_shift_pct": shift, "iv_used_pct": round(sig * 100, 2), "rows": []}
        for d in days_forward:
            t = max(dte - d, 0) / 365.0
            row = {"days_forward": d, "dte_remaining": max(dte - d, 0), "values": []}
            for px in prices:
                val = black_scholes(px, strike, t, risk_free, sig, option_type)
                pnl = (val - premium) * 100 * contracts
                row["values"].append({
                    "underlying": px,
                    "underlying_move_pct": round((px / underlying - 1) * 100, 1),
                    "option_value": round(val, 3),
                    "pnl_dollars": round(pnl, 2),
                    "pnl_pct": round((val / premium - 1) * 100, 1) if premium else None,
                })
            block["rows"].append(row)
        grid.append(block)

    return {
        "inputs": {"underlying": underlying, "strike": strike, "dte": dte,
                   "premium": premium, "iv_pct": iv_pct, "type": option_type,
                   "contracts": contracts, "risk_free_pct": risk_free * 100},
        "prices": prices, "days_forward": days_forward, "iv_shifts_pct": iv_shifts_pct,
        "grid": grid,
        "max_loss": round(premium * 100 * contracts, 2),
        "break_even_at_expiry": round(break_even(strike, premium, option_type), 3),
        "claim": Claim(
            statement=(f"Theoretical values for {contracts}x {strike} {option_type} "
                       f"({dte} DTE, premium {premium}) across {len(prices)} underlying "
                       f"prices, {len(days_forward)} dates and {len(iv_shifts_pct)} IV shifts."),
            evidence=EvidenceClass.MODEL_ESTIMATE,
            data_source="black-scholes",
            data_origin=DataOrigin.UNKNOWN,
            methodology="Black-Scholes revaluation at each grid point; IV held at the shifted "
                        "level for the whole path.",
            caveats=["THEORETICAL. These are not prices you can transact at.",
                     "Volatility is held constant within each block. In reality IV moves with "
                     "price, usually falling as the underlying rallies, which makes the "
                     "up-case values here optimistic for calls.",
                     "Dividends, early exercise and financing are not modelled."],
        ).to_dict(),
    }
