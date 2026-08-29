"""Statistical machinery.

Hand-rolled rather than pulled from SciPy so that (a) the system has no heavy
dependency and (b) every method used to justify a trading claim is visible and
auditable in this file.

Everything here returns a :class:`StatResult` carrying the estimate, the
interval, the sample size and -- crucially -- a verdict that is willing to say
"insufficient evidence".
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any, Callable, Sequence

import numpy as np

MIN_USEFUL_N = 30
MIN_SERIOUS_N = 100


@dataclasses.dataclass
class StatResult:
    name: str
    estimate: float
    n: int
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None
    method: str = ""
    effect_size: float | None = None
    verdict: str = ""
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# distributions
# ---------------------------------------------------------------------------

def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def normal_ppf(p: float) -> float:
    """Acklam's rational approximation to the inverse normal CDF (|err| < 1.15e-9)."""
    if not 0 < p < 1:
        raise ValueError("p must be in (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# ---------------------------------------------------------------------------
# proportions
# ---------------------------------------------------------------------------

def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> StatResult:
    """Wilson score interval for a binomial proportion.

    Preferred over the normal ("Wald") interval because it stays inside [0,1] and
    behaves at small n and extreme proportions -- exactly the cases where trading
    samples live.
    """
    if n <= 0:
        return StatResult("proportion", float("nan"), 0, None, None, None,
                          "Wilson score interval", verdict="INSUFFICIENT_DATA: no observations")
    p = successes / n
    z = normal_ppf(1 - (1 - confidence) / 2)
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return StatResult(
        "proportion", p, n, max(0.0, centre - half), min(1.0, centre + half), None,
        f"Wilson score interval at {confidence:.0%} confidence",
        verdict=sample_size_verdict(n))


def binomial_test(successes: int, n: int, p0: float = 0.5) -> StatResult:
    """Two-sided exact binomial test against a null proportion."""
    if n <= 0:
        return StatResult("binomial_test", float("nan"), 0, verdict="INSUFFICIENT_DATA")
    from math import comb
    obs = comb(n, successes) * p0 ** successes * (1 - p0) ** (n - successes)
    total = 0.0
    for k in range(n + 1):
        pk = comb(n, k) * p0 ** k * (1 - p0) ** (n - k)
        if pk <= obs * (1 + 1e-12):
            total += pk
    return StatResult("binomial_test", successes / n, n, p_value=min(1.0, total),
                      method=f"exact two-sided binomial test vs p0={p0}",
                      verdict=sample_size_verdict(n))


# ---------------------------------------------------------------------------
# resampling
# ---------------------------------------------------------------------------

def bootstrap_ci(data: Sequence[float], statistic: Callable[[np.ndarray], float] = np.mean,
                 n_boot: int = 5000, confidence: float = 0.95,
                 seed: int = 12345, name: str = "statistic") -> StatResult:
    """Percentile bootstrap confidence interval.

    Assumes the observations are exchangeable. Trading returns are usually
    autocorrelated and regime-clustered, so this interval is optimistic; that
    caveat is attached to the result rather than hidden.
    """
    x = np.asarray([v for v in data if np.isfinite(v)], dtype=float)
    n = len(x)
    if n < 3:
        return StatResult(name, float(statistic(x)) if n else float("nan"), n,
                          verdict="INSUFFICIENT_DATA: need at least 3 observations",
                          method="percentile bootstrap")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    boots = np.array([statistic(x[i]) for i in idx])
    lo, hi = np.percentile(boots, [(1 - confidence) / 2 * 100, (1 + confidence) / 2 * 100])
    return StatResult(name, float(statistic(x)), n, float(lo), float(hi), None,
                      f"percentile bootstrap, {n_boot} resamples, {confidence:.0%} interval",
                      verdict=sample_size_verdict(n),
                      detail={"caveat": "Bootstrap assumes exchangeable observations; "
                                        "clustered trades make this interval optimistic."})


def permutation_test(a: Sequence[float], b: Sequence[float],
                     statistic: Callable[[np.ndarray], float] = np.mean,
                     n_perm: int = 10_000, seed: int = 12345,
                     name: str = "difference") -> StatResult:
    """Two-sided permutation test on the difference of a statistic between groups.

    Makes no distributional assumption -- appropriate for fat-tailed R-multiples
    where a t-test's assumptions clearly fail.
    """
    x = np.asarray([v for v in a if np.isfinite(v)], float)
    y = np.asarray([v for v in b if np.isfinite(v)], float)
    if len(x) < 3 or len(y) < 3:
        return StatResult(name, float("nan"), len(x) + len(y),
                          verdict="INSUFFICIENT_DATA: each group needs at least 3 observations",
                          method="permutation test")
    obs = float(statistic(x) - statistic(y))
    pool = np.concatenate([x, y])
    nx = len(x)
    rng = np.random.default_rng(seed)
    count = 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        if abs(float(statistic(pool[:nx]) - statistic(pool[nx:]))) >= abs(obs) - 1e-15:
            count += 1
    p = (count + 1) / (n_perm + 1)
    return StatResult(name, obs, len(x) + len(y), p_value=p,
                      method=f"two-sided permutation test, {n_perm} shuffles",
                      effect_size=cohens_d(x, y),
                      verdict=sample_size_verdict(min(len(x), len(y))),
                      detail={"n_a": len(x), "n_b": len(y),
                              "stat_a": float(statistic(x)), "stat_b": float(statistic(y))})


def welch_t(a: Sequence[float], b: Sequence[float], name: str = "welch_t") -> StatResult:
    """Welch's unequal-variance t-test. Reported alongside the permutation test
    so a reader can see whether the normal-theory answer agrees."""
    x = np.asarray([v for v in a if np.isfinite(v)], float)
    y = np.asarray([v for v in b if np.isfinite(v)], float)
    if len(x) < 3 or len(y) < 3:
        return StatResult(name, float("nan"), len(x) + len(y), verdict="INSUFFICIENT_DATA")
    vx, vy = np.var(x, ddof=1) / len(x), np.var(y, ddof=1) / len(y)
    if vx + vy <= 0:
        return StatResult(name, 0.0, len(x) + len(y), verdict="degenerate: zero variance")
    t = (x.mean() - y.mean()) / math.sqrt(vx + vy)
    p = 2 * (1 - normal_cdf(abs(t)))   # normal approximation to the t distribution
    return StatResult(name, float(t), len(x) + len(y), p_value=float(p),
                      method="Welch's t (normal approximation to the null)",
                      effect_size=cohens_d(x, y), verdict=sample_size_verdict(min(len(x), len(y))))


def cohens_d(a: Sequence[float], b: Sequence[float]) -> float | None:
    x = np.asarray([v for v in a if np.isfinite(v)], float)
    y = np.asarray([v for v in b if np.isfinite(v)], float)
    if len(x) < 2 or len(y) < 2:
        return None
    sp = math.sqrt(((len(x) - 1) * np.var(x, ddof=1) + (len(y) - 1) * np.var(y, ddof=1))
                   / (len(x) + len(y) - 2))
    return float((x.mean() - y.mean()) / sp) if sp > 0 else None


# ---------------------------------------------------------------------------

def sample_size_verdict(n: int) -> str:
    if n < 10:
        return f"INSUFFICIENT_DATA: N={n} is too small to support any conclusion"
    if n < MIN_USEFUL_N:
        return f"WEAK: N={n} (<{MIN_USEFUL_N}); the interval will be very wide"
    if n < MIN_SERIOUS_N:
        return f"MODERATE: N={n} (<{MIN_SERIOUS_N})"
    return f"ADEQUATE_SAMPLE: N={n}"


def describe(values: Sequence[float], name: str = "values") -> dict[str, Any]:
    x = np.asarray([v for v in values if np.isfinite(v)], float)
    if len(x) == 0:
        return {"name": name, "n": 0}
    return {
        "name": name, "n": int(len(x)),
        "mean": float(x.mean()), "median": float(np.median(x)),
        "stdev": float(np.std(x, ddof=1)) if len(x) > 1 else 0.0,
        "min": float(x.min()), "max": float(x.max()),
        "p05": float(np.percentile(x, 5)), "p25": float(np.percentile(x, 25)),
        "p75": float(np.percentile(x, 75)), "p95": float(np.percentile(x, 95)),
        "pct_positive": float((x > 0).mean() * 100),
        "skew": float(((x - x.mean()) ** 3).mean() / (x.std() ** 3)) if x.std() > 0 else 0.0,
    }


def conclusion_from(p_value: float | None, n: int, effect: float | None,
                    alpha: float = 0.05) -> str:
    """Turn a test into one of the four verdicts the brief demands."""
    if n < MIN_USEFUL_N:
        return "INSUFFICIENT_DATA"
    if p_value is None:
        return "INSUFFICIENT_DATA"
    if p_value <= alpha and effect is not None and abs(effect) >= 0.2:
        return "SUPPORTED"
    if p_value <= alpha:
        return "MIXED"          # significant but trivially small effect
    if p_value <= 0.20:
        return "MIXED"
    return "UNSUPPORTED"
