"""Provider interfaces.

Nothing in the application depends on a concrete vendor. Engines depend on these
protocols; ``registry.py`` decides which implementation satisfies them at run
time based on configuration and reachability.
"""

from __future__ import annotations

import abc
import datetime as dt
from typing import Any, Protocol, runtime_checkable

from ..types import PriceSeries, Timeframe


class ProviderError(RuntimeError):
    pass


class ProviderUnavailable(ProviderError):
    """Raised when a provider is not configured or cannot be reached."""


class Provider(abc.ABC):
    name: str = "abstract"
    #: True when this provider returns generated (non-market) data.
    synthetic: bool = False

    @abc.abstractmethod
    def available(self) -> tuple[bool, str]:
        """Return (usable, human-readable reason)."""

    def describe(self) -> dict[str, Any]:
        ok, reason = self.available()
        return {"name": self.name, "available": ok, "reason": reason,
                "synthetic": self.synthetic, "capabilities": sorted(self.capabilities())}

    def capabilities(self) -> set[str]:
        caps: set[str] = set()
        for cap, base in (
            ("historical", HistoricalDataProvider),
            ("intraday", IntradayDataProvider),
            ("options", OptionsDataProvider),
            ("fundamentals", FundamentalDataProvider),
            ("news", NewsProvider),
            ("sectors", SectorDataProvider),
            ("quotes", MarketDataProvider),
        ):
            if isinstance(self, base):
                caps.add(cap)
        return caps


class HistoricalDataProvider(Provider):
    """Daily (and weekly) history."""

    @abc.abstractmethod
    def daily(self, symbol: str, start: dt.date | None = None,
              end: dt.date | None = None) -> PriceSeries: ...


class IntradayDataProvider(Provider):
    @abc.abstractmethod
    def intraday(self, symbol: str, timeframe: Timeframe,
                 start: dt.date | None = None, end: dt.date | None = None) -> PriceSeries: ...


class MarketDataProvider(Provider):
    @abc.abstractmethod
    def quote(self, symbol: str) -> dict[str, Any]: ...


class OptionsDataProvider(Provider):
    @abc.abstractmethod
    def chain(self, symbol: str, expiration: dt.date | None = None) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    def expirations(self, symbol: str) -> list[dt.date]: ...


class FundamentalDataProvider(Provider):
    @abc.abstractmethod
    def fundamentals(self, symbol: str) -> dict[str, Any]: ...


class NewsProvider(Provider):
    @abc.abstractmethod
    def news(self, symbol: str, since: dt.date | None = None) -> list[dict[str, Any]]: ...


class SectorDataProvider(Provider):
    @abc.abstractmethod
    def sector_map(self) -> dict[str, str]:
        """symbol -> sector name."""


@runtime_checkable
class BrokerProvider(Protocol):
    """Deliberately not implemented. Any implementation must honour the
    kill switch and the confirmation gate in ``tradingbrain.risk.guards``."""

    def account(self) -> dict[str, Any]: ...
    def positions(self) -> list[dict[str, Any]]: ...
    def submit(self, order: dict[str, Any], *, confirmed: bool) -> dict[str, Any]: ...
