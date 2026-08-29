"""Black-Scholes pricing and Greeks.

EVERY number produced here is a MODEL_ESTIMATE. Black-Scholes assumes constant
volatility, lognormal returns, continuous trading, no early exercise and no
transaction costs -- all false for American equity options. A theoretical value
is not a price you can trade at, and this module never claims otherwise.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

SQRT_2PI = math.sqrt(2.0 * math.pi)


def _n_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _n_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclasses.dataclass
class OptionQuote:
    symbol: str
    type: str                 # "call" | "put"
    strike: float
    expiration: str
    dte: int
    underlying: float
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume: float | None = None
    open_interest: float | None = None
    implied_volatility: float | None = None
    iv_rank: float | None = None
    iv_percentile: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None
    source: str = "unknown"
    is_model_estimate: bool = True

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.last

    @property
    def spread_pct(self) -> float | None:
        if self.bid is None or self.ask is None or not self.mid:
            return None
        return (self.ask - self.bid) / self.mid * 100.0

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["mid"] = self.mid
        d["spread_pct"] = self.spread_pct
        return d


def _d1_d2(s: float, k: float, t: float, r: float, sigma: float, q: float = 0.0):
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        return None, None
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    return d1, d1 - sigma * math.sqrt(t)


def black_scholes(s: float, k: float, t: float, r: float, sigma: float,
                  option_type: str = "call", q: float = 0.0) -> float:
    """Theoretical European option value. t is in years."""
    if t <= 0:
        return max(0.0, (s - k) if option_type == "call" else (k - s))
    if sigma <= 0:
        fwd = s * math.exp(-q * t) - k * math.exp(-r * t)
        return max(0.0, fwd if option_type == "call" else -fwd)
    d1, d2 = _d1_d2(s, k, t, r, sigma, q)
    if option_type == "call":
        return s * math.exp(-q * t) * _n_cdf(d1) - k * math.exp(-r * t) * _n_cdf(d2)
    return k * math.exp(-r * t) * _n_cdf(-d2) - s * math.exp(-q * t) * _n_cdf(-d1)


def greeks(s: float, k: float, t: float, r: float, sigma: float,
           option_type: str = "call", q: float = 0.0) -> dict[str, float]:
    """Greeks in conventional per-contract-unit terms.

    theta is per calendar day; vega is per 1 volatility point (0.01); rho is per
    1 percentage point of rate.
    """
    if t <= 0 or sigma <= 0:
        intrinsic_delta = (1.0 if s > k else 0.0) if option_type == "call" else \
                          (-1.0 if s < k else 0.0)
        return {"delta": intrinsic_delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    d1, d2 = _d1_d2(s, k, t, r, sigma, q)
    disc_q, disc_r = math.exp(-q * t), math.exp(-r * t)
    pdf = _n_pdf(d1)
    if option_type == "call":
        delta = disc_q * _n_cdf(d1)
        theta = (-s * disc_q * pdf * sigma / (2 * math.sqrt(t))
                 - r * k * disc_r * _n_cdf(d2) + q * s * disc_q * _n_cdf(d1))
        rho = k * t * disc_r * _n_cdf(d2) / 100.0
    else:
        delta = -disc_q * _n_cdf(-d1)
        theta = (-s * disc_q * pdf * sigma / (2 * math.sqrt(t))
                 + r * k * disc_r * _n_cdf(-d2) - q * s * disc_q * _n_cdf(-d1))
        rho = -k * t * disc_r * _n_cdf(-d2) / 100.0
    return {
        "delta": delta,
        "gamma": disc_q * pdf / (s * sigma * math.sqrt(t)),
        "theta": theta / 365.0,
        "vega": s * disc_q * pdf * math.sqrt(t) / 100.0,
        "rho": rho,
    }


def implied_volatility(price: float, s: float, k: float, t: float, r: float,
                       option_type: str = "call", q: float = 0.0,
                       tol: float = 1e-6, max_iter: int = 100) -> float | None:
    """Solve for sigma by bisection (robust where Newton diverges near the wings)."""
    if price <= 0 or t <= 0 or s <= 0 or k <= 0:
        return None
    intrinsic = max(0.0, (s * math.exp(-q * t) - k * math.exp(-r * t)) if option_type == "call"
                    else (k * math.exp(-r * t) - s * math.exp(-q * t)))
    if price < intrinsic - tol:
        return None                      # price below intrinsic: no solution exists
    lo, hi = 1e-6, 5.0
    if black_scholes(s, k, t, r, hi, option_type, q) < price:
        return None                      # beyond 500% vol; treat as unsolvable
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        val = black_scholes(s, k, t, r, mid, option_type, q)
        if abs(val - price) < tol:
            return mid
        if val > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def break_even(strike: float, premium: float, option_type: str = "call") -> float:
    return strike + premium if option_type == "call" else strike - premium
