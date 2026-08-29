"""Option chain access.

Real chains come from a configured provider (Polygon or Yahoo). When none is
reachable -- the case in this build environment -- a MODEL chain is generated
from the underlying's own realised volatility so the Options Lab is testable.

A model chain is stamped ``source="model"`` and ``is_model_estimate=True`` on
every contract, and the UI refuses to present it as market data.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np

from ..data.providers.base import OptionsDataProvider, ProviderUnavailable
from ..data.providers.registry import HUB, DataHub, availability, get_provider
from ..config import SETTINGS, Settings
from .pricing import OptionQuote, black_scholes, greeks

RISK_FREE = 0.04          # documented assumption; override per call
STRIKE_STEPS = (-0.30, -0.20, -0.15, -0.10, -0.05, -0.025, 0.0,
                0.025, 0.05, 0.10, 0.15, 0.20, 0.30)
DTE_LADDER = (7, 14, 30, 45, 60, 90, 120, 180)


def _options_provider(settings: Settings = SETTINGS) -> OptionsDataProvider | None:
    for name in settings.provider_order:
        try:
            p = get_provider(name)
        except KeyError:
            continue
        if isinstance(p, OptionsDataProvider):
            ok, _ = availability(p)
            if ok:
                return p
    return None


def chain_source(settings: Settings = SETTINGS) -> dict[str, Any]:
    p = _options_provider(settings)
    if p is None:
        return {"source": "model", "is_model_estimate": True,
                "detail": ("No options data provider is reachable. Chains are GENERATED "
                           "from the underlying's realised volatility with Black-Scholes. "
                           "Strikes, greeks and premiums are model estimates, not quotes. "
                           "Set POLYGON_API_KEY (or make Yahoo reachable) for real chains.")}
    return {"source": p.name, "is_model_estimate": False,
            "detail": f"Live option chain from {p.name}."}


def _realised_vol(hub: DataHub, symbol: str, window: int = 30,
                  as_of: dt.date | None = None) -> tuple[float, dict[str, Any]]:
    ser = hub.daily(symbol, end=as_of)
    c = ser.close
    if len(c) < window + 2:
        return 0.35, {"window": window, "note": "insufficient history; defaulted to 35%"}
    rets = np.diff(np.log(np.maximum(c[-(window + 1):], 1e-9)))
    vol = float(np.std(rets, ddof=1) * math.sqrt(252))
    # historical distribution of the same measure, for an IV-rank analogue
    all_rets = np.diff(np.log(np.maximum(c, 1e-9)))
    hist = np.array([np.std(all_rets[max(0, i - window):i], ddof=1) * math.sqrt(252)
                     for i in range(window, len(all_rets), 5)])
    hist = hist[np.isfinite(hist)]
    rank = float((vol - hist.min()) / (hist.max() - hist.min()) * 100) if len(hist) > 5 and \
        hist.max() > hist.min() else float("nan")
    pctile = float((hist <= vol).mean() * 100) if len(hist) else float("nan")
    return max(vol, 0.05), {
        "window": window, "realised_vol_annual": round(vol * 100, 2),
        "rank_of_realised_vol": round(rank, 1) if np.isfinite(rank) else None,
        "percentile_of_realised_vol": round(pctile, 1) if np.isfinite(pctile) else None,
        "note": ("This is REALISED volatility standing in for implied volatility. They are "
                 "different quantities: implied vol usually trades above realised. Every "
                 "premium and greek below inherits that substitution."),
    }


def expirations(symbol: str, hub: DataHub = HUB,
                as_of: dt.date | None = None) -> list[str]:
    p = _options_provider()
    if p is not None:
        try:
            return [d.isoformat() for d in p.expirations(symbol)]
        except ProviderUnavailable:
            pass
    base = as_of or dt.date.today()
    out = []
    for dte in DTE_LADDER:
        d = base + dt.timedelta(days=dte)
        # roll to the nearest Friday, the standard monthly/weekly expiry
        d += dt.timedelta(days=(4 - d.weekday()) % 7)
        out.append(d.isoformat())
    return sorted(set(out))


def get_chain(symbol: str, expiration: str | None = None, hub: DataHub = HUB,
              as_of: dt.date | None = None,
              risk_free: float = RISK_FREE) -> dict[str, Any]:
    src = chain_source()
    ser = hub.daily(symbol, end=as_of)
    if len(ser) == 0:
        raise ProviderUnavailable(f"no underlying data for {symbol}")
    spot = float(ser.close[-1])
    spot_date = ser.ts[-1].date()

    p = _options_provider()
    if p is not None:
        try:
            exp = dt.date.fromisoformat(expiration) if expiration else None
            raw = p.chain(symbol, exp)
            quotes = []
            for c in raw:
                e = c.get("expiration")
                edate = dt.date.fromisoformat(e) if isinstance(e, str) else e
                dte = (edate - spot_date).days if edate else 0
                quotes.append(OptionQuote(
                    symbol=symbol.upper(), type=c.get("type", "call"),
                    strike=float(c.get("strike") or 0), expiration=str(e), dte=dte,
                    underlying=spot, bid=c.get("bid"), ask=c.get("ask"),
                    last=c.get("last"), volume=c.get("volume"),
                    open_interest=c.get("open_interest"),
                    implied_volatility=c.get("implied_volatility"),
                    delta=c.get("delta"), gamma=c.get("gamma"), theta=c.get("theta"),
                    vega=c.get("vega"), source=p.name, is_model_estimate=False))
            return {"symbol": symbol.upper(), "underlying": spot,
                    "as_of": spot_date.isoformat(), "source": src,
                    "contracts": [q.to_dict() for q in quotes],
                    "volatility_basis": {"note": "implied volatility as reported by the vendor"}}
        except ProviderUnavailable:
            src = chain_source()

    # ---- model chain ----------------------------------------------------
    sigma, volmeta = _realised_vol(hub, symbol, as_of=as_of)
    exps = [expiration] if expiration else expirations(symbol, hub, spot_date)
    quotes: list[OptionQuote] = []
    for e in exps:
        edate = dt.date.fromisoformat(e)
        dte = max((edate - spot_date).days, 0)
        t = dte / 365.0
        for step in STRIKE_STEPS:
            k = round(spot * (1 + step), 2)
            k = _round_strike(k)
            for kind in ("call", "put"):
                theo = black_scholes(spot, k, t, risk_free, sigma, kind)
                g = greeks(spot, k, t, risk_free, sigma, kind)
                # a model spread that widens with moneyness and shortens with size
                width = max(0.02, theo * 0.04 + 0.03 + abs(step) * 0.35)
                quotes.append(OptionQuote(
                    symbol=symbol.upper(), type=kind, strike=k, expiration=e, dte=dte,
                    underlying=spot,
                    bid=round(max(theo - width / 2, 0.0), 2),
                    ask=round(theo + width / 2, 2),
                    last=round(theo, 2),
                    volume=None, open_interest=None,
                    implied_volatility=round(sigma, 4),
                    iv_rank=volmeta.get("rank_of_realised_vol"),
                    iv_percentile=volmeta.get("percentile_of_realised_vol"),
                    delta=round(g["delta"], 4), gamma=round(g["gamma"], 6),
                    theta=round(g["theta"], 4), vega=round(g["vega"], 4),
                    rho=round(g["rho"], 4), source="model", is_model_estimate=True))
    return {"symbol": symbol.upper(), "underlying": spot, "as_of": spot_date.isoformat(),
            "source": src, "contracts": [q.to_dict() for q in quotes],
            "volatility_basis": volmeta,
            "model_assumptions": [
                f"Black-Scholes European pricing at a {risk_free:.1%} risk-free rate, "
                "no dividends, constant volatility.",
                "Implied volatility is substituted by 30-day realised volatility.",
                "Bid/ask is a synthetic spread that widens with moneyness. Volume and open "
                "interest are unavailable and shown as null rather than invented.",
                "American early exercise is not modelled.",
            ]}


def _round_strike(k: float) -> float:
    if k >= 200:
        return round(k / 5) * 5
    if k >= 50:
        return round(k / 2.5) * 2.5
    if k >= 20:
        return round(k)
    return round(k * 2) / 2
