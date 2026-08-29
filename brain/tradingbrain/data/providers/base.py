"""Provider interfaces.

Nothing in the application depends on a concrete vendor. Engines depend on these
protocols; ``registry.py`` decides which implementation satisfies them at run
time based on configuration and reachability.
"""

from __future__ import annotations

import abc
import datetime as dt
import threading
from typing import Any, Callable, Protocol, runtime_checkable

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
            ("realtime", RealtimeMarketDataProvider),
            ("corporate_actions", CorporateActionsProvider),
            ("universe", SymbolUniverseProvider),
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


class RealtimeMarketDataProvider(Provider):
    """Streaming trades/quotes/bars.

    The contract is a callback pump, not a generator, so the ingestion service
    owns the reconnect loop and the provider only has to know the wire format.
    ``feed_label`` must state exactly what the stream covers -- a free Alpaca
    key streams IEX only, which is a fraction of consolidated US volume, and
    calling that "real-time market data" without qualification is how a scanner
    silently ends up wrong.
    """

    @abc.abstractmethod
    def feed_label(self) -> str: ...

    @abc.abstractmethod
    def stream(self, symbols: list[str], on_message: Callable[[dict[str, Any]], None],
               channels: tuple[str, ...] = ("bars",),
               stop: "threading.Event | None" = None) -> None:
        """Block, pumping decoded messages into ``on_message`` until ``stop`` is set."""


class CorporateActionsProvider(Provider):
    @abc.abstractmethod
    def corporate_actions(self, symbol: str, start: dt.date | None = None,
                          end: dt.date | None = None) -> list[dict[str, Any]]:
        """Splits, dividends and similar, each with an effective date and a factor."""


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


class SymbolUniverseProvider(Provider):
    """The tradeable universe, so the screener is not limited to a watchlist."""

    @abc.abstractmethod
    def list_symbols(self, active_only: bool = True) -> list[dict[str, Any]]:
        """Rows of {symbol, name, exchange, asset_class, tradable, status, ...}."""


@runtime_checkable
class BrokerProvider(Protocol):
    """Deliberately not implemented. Any implementation must honour the
    kill switch and the confirmation gate in ``tradingbrain.risk.guards``."""

    def account(self) -> dict[str, Any]: ...
    def positions(self) -> list[dict[str, Any]]: ...
    def submit(self, order: dict[str, Any], *, confirmed: bool) -> dict[str, Any]: ...
