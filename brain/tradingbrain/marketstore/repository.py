"""Database-backed provider: the read path every engine actually uses.

This is what makes "the database is the source of truth" real rather than
aspirational. It satisfies the same provider protocols as a vendor adapter, so
the DataHub, backtester, scanner and analyzer cannot tell the difference -- but
it never makes a network call.

A miss here does not fall through to a vendor silently. The DataHub asks the
ingestion service to fetch and PERSIST the missing range, then reads it back
from this provider, so the same bars are never bought twice.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..data.providers.base import (HistoricalDataProvider, IntradayDataProvider,
                                   ProviderUnavailable, SectorDataProvider,
                                   SymbolUniverseProvider)
from ..data.types import PriceSeries, Timeframe
from .store import MARKET, MarketStore


class DbProvider(HistoricalDataProvider, IntradayDataProvider, SectorDataProvider,
                 SymbolUniverseProvider):
    name = "db"
    synthetic = False

    def __init__(self, store: MarketStore = MARKET) -> None:
        self.store = store

    def available(self) -> tuple[bool, str]:
        stats = self.store.stats()
        rows = stats["row_counts"]["market_data_daily"]
        if rows == 0:
            return False, (f"the market database at {self.store.path} holds no daily bars "
                           "yet. Run `python -m tradingbrain.cli data bootstrap` (or "
                           "`data backfill`) to populate it.")
        return True, (f"{rows:,} daily bars for {stats['active_symbols']} symbols "
                      f"({stats['size_mb']} MB)")

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        stats = self.store.stats()
        d.update(rows=stats["row_counts"], size_mb=stats["size_mb"],
                 bars_by_provider=stats["daily_bars_by_provider"],
                 note="local store; no network calls")
        return d

    # -- reads -------------------------------------------------------------
    def daily(self, symbol: str, start: dt.date | None = None,
              end: dt.date | None = None) -> PriceSeries:
        s = self.store.bars(symbol, "1d", start, end)
        if len(s) == 0:
            raise ProviderUnavailable(
                f"no daily bars stored for {symbol}. Backfill it with "
                f"`python -m tradingbrain.cli data backfill {symbol}`.")
        return s

    def intraday(self, symbol: str, timeframe: Timeframe, start: dt.date | None = None,
                 end: dt.date | None = None) -> PriceSeries:
        s = self.store.bars(symbol, timeframe.value, start, end)
        if len(s) == 0:
            raise ProviderUnavailable(
                f"no {timeframe.value} bars stored for {symbol}")
        return s

    # -- universe ----------------------------------------------------------
    def symbols(self, timeframe: Timeframe = Timeframe.D1) -> list[str]:
        """Only symbols that actually have bars -- an empty row is not coverage."""
        table = {"1d": "market_data_daily", "1m": "market_data_1m",
                 "5m": "market_data_5m", "15m": "market_data_15m",
                 "1h": "market_data_1h"}[timeframe.value]
        return [r["symbol"] for r in self.store.query(
            f"SELECT DISTINCT s.symbol FROM {table} b JOIN symbols s ON s.id=b.symbol_id "
            "ORDER BY s.symbol")]

    def list_symbols(self, active_only: bool = True) -> list[dict[str, Any]]:
        return self.store.symbols(active_only=active_only)

    def sector_map(self) -> dict[str, str]:
        return self.store.sector_map()

    def themes(self) -> dict[str, list[str]]:
        """Themes live with the sector taxonomy, not in market data.

        Returned from the store's kv so the operator can edit them without a
        code change; falls back to the built-in list on first run.
        """
        stored = self.store.kv_get("themes")
        if stored:
            return {k: list(v) for k, v in stored.items()}
        from ..data.providers.synthetic import THEMES
        return {k: list(v) for k, v in THEMES.items()}
