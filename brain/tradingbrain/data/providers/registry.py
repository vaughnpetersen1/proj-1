"""Provider selection and the single data access point used by every engine."""

from __future__ import annotations

import datetime as dt
import functools
import threading
from typing import Any

from ...config import SETTINGS, Settings
from ...provenance import DataOrigin
from ..types import PriceSeries, Timeframe
from .base import Provider, ProviderUnavailable
from .csv_cache import CsvCacheProvider
from .http_vendors import PolygonProvider, StooqProvider, TiingoProvider, YahooProvider
from .synthetic import SECTOR_ETF, THEMES, SyntheticProvider

_LOCK = threading.Lock()
_AVAIL_TTL_SECONDS = 300.0
_avail_cache: dict[str, tuple[float, tuple[bool, str]]] = {}


def availability(p: Provider, force: bool = False) -> tuple[bool, str]:
    """Cached provider reachability.

    Without this, every symbol lookup re-probes every unreachable vendor over the
    network. On a blocked-egress machine that turned a 96-symbol breadth scan
    into 40 seconds of connection timeouts.
    """
    import time
    now = time.monotonic()
    hit = _avail_cache.get(p.name)
    if hit and not force and now - hit[0] < _AVAIL_TTL_SECONDS:
        return hit[1]
    res = p.available()
    _avail_cache[p.name] = (now, res)
    return res


def reset_availability_cache() -> None:
    _avail_cache.clear()


def build_providers(settings: Settings = SETTINGS) -> dict[str, Provider]:
    return {
        "csv": CsvCacheProvider(settings.cache_dir),
        "stooq": StooqProvider(),
        "yahoo": YahooProvider(),
        "tiingo": TiingoProvider(settings.tiingo_api_key),
        "polygon": PolygonProvider(settings.polygon_api_key),
        "synthetic": SyntheticProvider(),
    }


@functools.lru_cache(maxsize=1)
def _providers() -> dict[str, Provider]:
    return build_providers()


def get_provider(name: str) -> Provider:
    p = _providers().get(name)
    if p is None:
        raise KeyError(f"unknown provider {name!r}; known: {sorted(_providers())}")
    return p


def provider_status(settings: Settings = SETTINGS) -> list[dict[str, Any]]:
    out = []
    for name in settings.provider_order:
        try:
            p = get_provider(name)
        except KeyError:
            out.append({"name": name, "available": False, "reason": "not registered",
                        "synthetic": False, "capabilities": []})
            continue
        ok, reason = availability(p)
        d = p.describe()
        d.update(available=ok, reason=reason)
        out.append(d)
    return out


class DataHub:
    """Resolves a request to the first provider that can serve it.

    Every returned series carries ``provider`` and ``origin`` so downstream
    Claims can state exactly where a number came from. Results are memoised per
    process because backtests hit the same symbols thousands of times.
    """

    def __init__(self, settings: Settings = SETTINGS) -> None:
        self.settings = settings
        self._cache: dict[tuple, PriceSeries] = {}
        self._resolved: dict[str, str] = {}

    # -- introspection -----------------------------------------------------
    def status(self) -> list[dict[str, Any]]:
        return provider_status(self.settings)

    def active_daily_provider(self) -> str:
        for name in self.settings.provider_order:
            try:
                p = get_provider(name)
            except KeyError:
                continue
            if not hasattr(p, "daily"):
                continue
            ok, _ = availability(p)
            if ok:
                return name
        return "synthetic"

    # -- data --------------------------------------------------------------
    def daily(self, symbol: str, start: dt.date | None = None,
              end: dt.date | None = None, provider: str | None = None) -> PriceSeries:
        key = ("d", symbol.upper(), provider)
        with _LOCK:
            hit = self._cache.get(key)
        if hit is None:
            hit = self._fetch_daily(symbol, provider)
            with _LOCK:
                self._cache[key] = hit
        return hit.between(start, end)

    def _fetch_daily(self, symbol: str, provider: str | None) -> PriceSeries:
        errors: list[str] = []
        order = (provider,) if provider else self.settings.provider_order
        for name in order:
            try:
                p = get_provider(name)
            except KeyError as exc:
                errors.append(str(exc))
                continue
            if not hasattr(p, "daily"):
                continue
            ok, reason = availability(p)
            if not ok:
                errors.append(f"{name}: {reason}")
                continue
            try:
                series = p.daily(symbol)
                if len(series) == 0:
                    errors.append(f"{name}: empty series")
                    continue
                self._resolved[symbol.upper()] = name
                return series
            except (ProviderUnavailable, KeyError, ValueError) as exc:
                errors.append(f"{name}: {exc}")
        raise ProviderUnavailable(
            f"No provider could supply daily bars for {symbol}. Tried -> "
            + " | ".join(errors)
        )

    def intraday(self, symbol: str, timeframe: Timeframe, start: dt.date | None = None,
                 end: dt.date | None = None, provider: str | None = None) -> PriceSeries:
        key = ("i", symbol.upper(), timeframe.value, provider, start, end)
        with _LOCK:
            hit = self._cache.get(key)
        if hit is not None:
            return hit
        errors: list[str] = []
        order = (provider,) if provider else self.settings.provider_order
        for name in order:
            try:
                p = get_provider(name)
            except KeyError:
                continue
            if not hasattr(p, "intraday"):
                continue
            ok, reason = availability(p)
            if not ok:
                errors.append(f"{name}: {reason}")
                continue
            try:
                series = p.intraday(symbol, timeframe, start, end)
                if len(series):
                    with _LOCK:
                        self._cache[key] = series
                    return series
                errors.append(f"{name}: empty series")
            except (ProviderUnavailable, KeyError, ValueError) as exc:
                errors.append(f"{name}: {exc}")
        raise ProviderUnavailable(
            f"No provider could supply {timeframe.value} bars for {symbol}. Tried -> "
            + " | ".join(errors))

    # -- universe ----------------------------------------------------------
    def universe(self) -> list[str]:
        """Every symbol the active data path can actually serve."""
        syms: list[str] = []
        for name in self.settings.provider_order:
            try:
                p = get_provider(name)
            except KeyError:
                continue
            ok, _ = availability(p)
            if ok and hasattr(p, "symbols"):
                syms.extend(p.symbols())          # type: ignore[attr-defined]
        seen, out = set(), []
        for s in syms:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def sector_map(self) -> dict[str, str]:
        for name in self.settings.provider_order:
            try:
                p = get_provider(name)
            except KeyError:
                continue
            ok, _ = availability(p)
            if ok and hasattr(p, "sector_map"):
                return p.sector_map()             # type: ignore[attr-defined]
        return {}

    def sector_etfs(self) -> dict[str, str]:
        return dict(SECTOR_ETF)

    def themes(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in THEMES.items()}

    def origin_of(self, symbol: str) -> DataOrigin:
        try:
            return self.daily(symbol).origin
        except ProviderUnavailable:
            return DataOrigin.UNKNOWN

    def data_note(self) -> str:
        prov = self.active_daily_provider()
        if prov == "synthetic":
            return ("ACTIVE DATA PATH: synthetic generator. Every statistic below is "
                    "computed on generated data and is NOT evidence about real markets. "
                    "Supply real bars via CSVs in the cache directory or an API key.")
        return f"ACTIVE DATA PATH: {prov} (real vendor data)."


HUB = DataHub()
