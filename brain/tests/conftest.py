"""Deterministic fixtures.

The whole point of these tests is that they do not depend on market data, a
network, or the generator's random state. Every price series here is written by
hand or by an explicit formula, so an assertion failure means the code changed,
not the data.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tradingbrain.data.types import Bar, PriceSeries, Timeframe   # noqa: E402
from tradingbrain.provenance import DataOrigin                    # noqa: E402


def make_series(closes, symbol="TEST", start=dt.date(2020, 1, 1), volumes=None,
                spread=0.01, origin=DataOrigin.REAL, adjusted=True) -> PriceSeries:
    """Build an OHLCV series from a list of closes with a deterministic shape.

    open  = previous close, high/low straddle the bar by `spread` of price.
    """
    closes = [float(c) for c in closes]
    vols = volumes if volumes is not None else [1_000_000.0] * len(closes)
    bars = []
    d = start
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        hi = max(o, c) * (1 + spread)
        lo = min(o, c) * (1 - spread)
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        bars.append(Bar(dt.datetime.combine(d, dt.time(16, 0)), o, hi, lo, c, float(vols[i])))
        d += dt.timedelta(days=1)
    return PriceSeries.from_bars(symbol, Timeframe.D1, bars, provider="test",
                                 origin=origin, adjusted=adjusted)


@pytest.fixture
def ramp():
    """A clean 1%-per-bar uptrend: 120 bars."""
    return make_series([100 * (1.01 ** i) for i in range(120)])


@pytest.fixture
def flat():
    return make_series([100.0] * 80)


@pytest.fixture
def setup_series():
    """Expansion -> tightening low-volume base -> high-volume breakout.

    Bars 0-39   flat at 100
    Bars 40-59  +2.0%/bar advance (approximately +48%)
    Bars 60-79  a base: oscillation that halves in amplitude, volume halving
    Bar  80     breakout above the base high on 3x volume
    Bars 81-110 continuation
    """
    closes, vols = [], []
    for i in range(40):
        closes.append(100.0); vols.append(1_000_000.0)
    px = 100.0
    for i in range(20):
        px *= 1.02
        closes.append(px); vols.append(1_600_000.0)
    top = px
    for i in range(20):
        amp = 0.045 * (1 - i / 22)                 # amplitude contracts
        closes.append(top * (1 - amp * (0.5 + 0.5 * ((-1) ** i))))
        vols.append(1_400_000.0 * (1 - 0.5 * i / 20))   # volume dries up
    base_high = max(closes[60:80]) * 1.012
    closes.append(base_high * 1.02); vols.append(5_000_000.0)   # bar 80: breakout
    px = closes[-1]
    for i in range(30):
        px *= 1.008
        closes.append(px); vols.append(1_500_000.0)
    return make_series(closes, symbol="BRKT", volumes=vols, spread=0.004)


@pytest.fixture
def tmp_store(tmp_path):
    from tradingbrain.db.store import Store
    return Store(tmp_path / "test.db")


@pytest.fixture
def csv_hub(tmp_path, setup_series, ramp):
    """A DataHub whose only provider is a CSV cache holding known series."""
    from tradingbrain.config import Settings
    from tradingbrain.data.providers.csv_cache import CsvCacheProvider
    from tradingbrain.data.providers import registry as reg

    cache = tmp_path / "cache"
    writer = CsvCacheProvider(cache)
    writer.write(setup_series)
    spy = make_series([100 * (1.0005 ** i) for i in range(len(setup_series))], symbol="SPY")
    qqq = make_series([100 * (1.0006 ** i) for i in range(len(setup_series))], symbol="QQQ")
    writer.write(spy)
    writer.write(qqq)
    writer.write(ramp)

    settings = Settings()
    settings.cache_dir = cache
    settings.provider_order = ("csv",)
    reg._providers.cache_clear()
    reg.reset_availability_cache()
    old = reg.build_providers

    def only_csv(_s=None):
        return {"csv": CsvCacheProvider(cache)}
    reg.build_providers = only_csv
    hub = reg.DataHub(settings)
    yield hub
    reg.build_providers = old
    reg._providers.cache_clear()
    reg.reset_availability_cache()
