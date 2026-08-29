"""Live vendor adapters (Stooq, Yahoo, Tiingo, Polygon).

None of these can reach the network in the environment this repo was built in
(the egress proxy denies CONNECT to market-data hosts by organisation policy),
so they are written against each vendor's documented response shape and fail
loudly and specifically rather than pretending to work. Run
``python -m tradingbrain.cli providers`` on a machine with egress to see which
ones light up.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ...provenance import DataOrigin
from ..types import Bar, PriceSeries, Timeframe
from .base import (HistoricalDataProvider, IntradayDataProvider, OptionsDataProvider,
                   ProviderUnavailable)

_UA = {"User-Agent": "trading-brain/1.0 (research; contact: repo owner)"}
_TIMEOUT = 20


def _get(url: str, headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(url, headers={**_UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise ProviderUnavailable(f"HTTP {exc.code} from {urllib.parse.urlparse(url).netloc}") from exc
    except Exception as exc:  # noqa: BLE001 - network stack raises many types
        raise ProviderUnavailable(f"{type(exc).__name__} contacting "
                                  f"{urllib.parse.urlparse(url).netloc}: {exc}") from exc


def _reachable(host_url: str) -> tuple[bool, str]:
    try:
        _get(host_url)
        return True, "reachable"
    except ProviderUnavailable as exc:
        return False, str(exc)


class StooqProvider(HistoricalDataProvider):
    """Free daily EOD bars, no API key. https://stooq.com"""

    name = "stooq"

    def available(self) -> tuple[bool, str]:
        ok, why = _reachable("https://stooq.com/q/d/l/?s=spy.us&i=d")
        return ok, ("Free daily EOD, no key required." if ok
                    else f"Not reachable ({why}). Requires outbound HTTPS to stooq.com.")

    def daily(self, symbol, start=None, end=None) -> PriceSeries:
        sym = symbol.lower()
        suffix = "" if "." in sym else ".us"
        raw = _get(f"https://stooq.com/q/d/l/?s={sym}{suffix}&i=d").decode("utf-8", "replace")
        rows = list(csv.DictReader(io.StringIO(raw)))
        if not rows or "Close" not in (rows[0] if rows else {}):
            raise ProviderUnavailable(f"stooq returned no usable data for {symbol}")
        bars = [
            Bar(dt.datetime.strptime(r["Date"], "%Y-%m-%d").replace(hour=16),
                float(r["Open"]), float(r["High"]), float(r["Low"]),
                float(r["Close"]), float(r.get("Volume") or 0))
            for r in rows if r.get("Close") not in (None, "", "N/A")
        ]
        return PriceSeries.from_bars(symbol, Timeframe.D1, bars, provider=self.name,
                                     origin=DataOrigin.REAL, adjusted=True).between(start, end)


class YahooProvider(HistoricalDataProvider, IntradayDataProvider, OptionsDataProvider):
    """Yahoo Finance public chart/options JSON. No key; rate limited; unofficial."""

    name = "yahoo"
    _BASE = "https://query1.finance.yahoo.com"

    def available(self) -> tuple[bool, str]:
        ok, why = _reachable(f"{self._BASE}/v8/finance/chart/SPY?range=5d&interval=1d")
        return ok, ("Public chart API reachable (unofficial, rate limited)." if ok
                    else f"Not reachable ({why}). Requires outbound HTTPS to query1.finance.yahoo.com.")

    _INTERVAL = {Timeframe.M1: "1m", Timeframe.M5: "5m", Timeframe.M15: "15m",
                 Timeframe.H1: "60m", Timeframe.D1: "1d", Timeframe.W1: "1wk"}

    def _chart(self, symbol: str, timeframe: Timeframe, rng: str) -> PriceSeries:
        url = (f"{self._BASE}/v8/finance/chart/{urllib.parse.quote(symbol)}"
               f"?range={rng}&interval={self._INTERVAL[timeframe]}&events=div%2Csplit")
        payload = json.loads(_get(url))
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            raise ProviderUnavailable(f"yahoo returned no result for {symbol}")
        r = result[0]
        stamps = r.get("timestamp") or []
        q = (r.get("indicators", {}).get("quote") or [{}])[0]
        adj = (r.get("indicators", {}).get("adjclose") or [{}])
        adjclose = adj[0].get("adjclose") if adj else None
        bars = []
        for i, ts in enumerate(stamps):
            o, h, l, c, v = (q.get("open"), q.get("high"), q.get("low"),
                             q.get("close"), q.get("volume"))
            vals = [o[i] if o else None, h[i] if h else None, l[i] if l else None,
                    c[i] if c else None]
            if any(x is None for x in vals):
                continue
            close = adjclose[i] if adjclose and adjclose[i] is not None else vals[3]
            ratio = close / vals[3] if vals[3] else 1.0
            bars.append(Bar(dt.datetime.utcfromtimestamp(ts),
                            vals[0] * ratio, vals[1] * ratio, vals[2] * ratio, close,
                            float((v[i] if v and v[i] is not None else 0))))
        if not bars:
            raise ProviderUnavailable(f"yahoo returned no complete candles for {symbol}")
        return PriceSeries.from_bars(symbol, timeframe, bars, provider=self.name,
                                     origin=DataOrigin.REAL, adjusted=True)

    def daily(self, symbol, start=None, end=None) -> PriceSeries:
        return self._chart(symbol, Timeframe.D1, "max").between(start, end)

    def intraday(self, symbol, timeframe, start=None, end=None) -> PriceSeries:
        rng = {"1m": "7d", "5m": "60d", "15m": "60d", "60m": "730d"}[self._INTERVAL[timeframe]]
        return self._chart(symbol, timeframe, rng).between(start, end)

    def expirations(self, symbol: str) -> list[dt.date]:
        payload = json.loads(_get(f"{self._BASE}/v7/finance/options/{symbol}"))
        res = (payload.get("optionChain") or {}).get("result") or []
        if not res:
            raise ProviderUnavailable(f"yahoo returned no option chain for {symbol}")
        return [dt.datetime.utcfromtimestamp(t).date() for t in res[0].get("expirationDates", [])]

    def chain(self, symbol: str, expiration: dt.date | None = None) -> list[dict[str, Any]]:
        url = f"{self._BASE}/v7/finance/options/{symbol}"
        if expiration:
            url += f"?date={int(dt.datetime.combine(expiration, dt.time()).timestamp())}"
        payload = json.loads(_get(url))
        res = (payload.get("optionChain") or {}).get("result") or []
        if not res:
            raise ProviderUnavailable(f"yahoo returned no option chain for {symbol}")
        out: list[dict[str, Any]] = []
        for group in res[0].get("options", []):
            exp = dt.datetime.utcfromtimestamp(group["expirationDate"]).date()
            for kind in ("calls", "puts"):
                for c in group.get(kind, []):
                    out.append({
                        "symbol": symbol.upper(), "type": kind[:-1], "expiration": exp.isoformat(),
                        "strike": c.get("strike"), "bid": c.get("bid"), "ask": c.get("ask"),
                        "last": c.get("lastPrice"), "volume": c.get("volume"),
                        "open_interest": c.get("openInterest"),
                        "implied_volatility": c.get("impliedVolatility"),
                        "in_the_money": c.get("inTheMoney"), "source": self.name,
                    })
        return out


class TiingoProvider(HistoricalDataProvider, IntradayDataProvider):
    name = "tiingo"

    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "TIINGO_API_KEY not set (see .env.example)."
        ok, why = _reachable("https://api.tiingo.com/api/test")
        return ok, "key set and API reachable" if ok else f"key set but {why}"

    def _hdr(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderUnavailable("TIINGO_API_KEY not set")
        return {"Authorization": f"Token {self.api_key}", "Content-Type": "application/json"}

    def daily(self, symbol, start=None, end=None) -> PriceSeries:
        s = (start or dt.date(2000, 1, 1)).isoformat()
        e = (end or dt.date.today()).isoformat()
        url = (f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
               f"?startDate={s}&endDate={e}&format=json")
        rows = json.loads(_get(url, self._hdr()))
        bars = [Bar(dt.datetime.fromisoformat(r["date"].replace("Z", "+00:00")).replace(tzinfo=None),
                    r["adjOpen"], r["adjHigh"], r["adjLow"], r["adjClose"], float(r["adjVolume"]))
                for r in rows]
        if not bars:
            raise ProviderUnavailable(f"tiingo returned no rows for {symbol}")
        return PriceSeries.from_bars(symbol, Timeframe.D1, bars, provider=self.name,
                                     origin=DataOrigin.REAL, adjusted=True)

    _FREQ = {Timeframe.M1: "1min", Timeframe.M5: "5min", Timeframe.M15: "15min",
             Timeframe.H1: "1hour"}

    def intraday(self, symbol, timeframe, start=None, end=None) -> PriceSeries:
        if timeframe not in self._FREQ:
            raise ProviderUnavailable(f"tiingo adapter does not map {timeframe.value}")
        s = (start or dt.date.today() - dt.timedelta(days=30)).isoformat()
        e = (end or dt.date.today()).isoformat()
        url = (f"https://api.tiingo.com/iex/{symbol}/prices?startDate={s}&endDate={e}"
               f"&resampleFreq={self._FREQ[timeframe]}&format=json")
        rows = json.loads(_get(url, self._hdr()))
        bars = [Bar(dt.datetime.fromisoformat(r["date"].replace("Z", "+00:00")).replace(tzinfo=None),
                    r["open"], r["high"], r["low"], r["close"], float(r.get("volume") or 0))
                for r in rows]
        if not bars:
            raise ProviderUnavailable(f"tiingo returned no intraday rows for {symbol}")
        return PriceSeries.from_bars(symbol, timeframe, bars, provider=self.name,
                                     origin=DataOrigin.REAL)


class PolygonProvider(HistoricalDataProvider, IntradayDataProvider, OptionsDataProvider):
    name = "polygon"
    _BASE = "https://api.polygon.io"

    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "POLYGON_API_KEY not set (see .env.example)."
        ok, why = _reachable(f"{self._BASE}/v3/reference/tickers?limit=1&apiKey={self.api_key}")
        return ok, "key set and API reachable" if ok else f"key set but {why}"

    def _aggs(self, symbol: str, mult: int, span: str, start: dt.date, end: dt.date,
              timeframe: Timeframe) -> PriceSeries:
        if not self.api_key:
            raise ProviderUnavailable("POLYGON_API_KEY not set")
        url = (f"{self._BASE}/v2/aggs/ticker/{symbol.upper()}/range/{mult}/{span}/"
               f"{start.isoformat()}/{end.isoformat()}?adjusted=true&sort=asc&limit=50000"
               f"&apiKey={self.api_key}")
        payload = json.loads(_get(url))
        rows = payload.get("results") or []
        bars = [Bar(dt.datetime.utcfromtimestamp(r["t"] / 1000.0), r["o"], r["h"], r["l"],
                    r["c"], float(r.get("v") or 0)) for r in rows]
        if not bars:
            raise ProviderUnavailable(f"polygon returned no aggregates for {symbol}")
        return PriceSeries.from_bars(symbol, timeframe, bars, provider=self.name,
                                     origin=DataOrigin.REAL, adjusted=True)

    def daily(self, symbol, start=None, end=None) -> PriceSeries:
        return self._aggs(symbol, 1, "day", start or dt.date(2005, 1, 1),
                          end or dt.date.today(), Timeframe.D1)

    def intraday(self, symbol, timeframe, start=None, end=None) -> PriceSeries:
        mult = {Timeframe.M1: 1, Timeframe.M5: 5, Timeframe.M15: 15, Timeframe.H1: 1}[timeframe]
        span = "hour" if timeframe is Timeframe.H1 else "minute"
        return self._aggs(symbol, mult, span, start or dt.date.today() - dt.timedelta(days=30),
                          end or dt.date.today(), timeframe)

    def expirations(self, symbol: str) -> list[dt.date]:
        return sorted({dt.date.fromisoformat(c["expiration"]) for c in self.chain(symbol)})

    def chain(self, symbol: str, expiration: dt.date | None = None) -> list[dict[str, Any]]:
        if not self.api_key:
            raise ProviderUnavailable("POLYGON_API_KEY not set")
        url = (f"{self._BASE}/v3/snapshot/options/{symbol.upper()}?limit=250"
               f"&apiKey={self.api_key}")
        if expiration:
            url += f"&expiration_date={expiration.isoformat()}"
        payload = json.loads(_get(url))
        out = []
        for r in payload.get("results") or []:
            det = r.get("details", {})
            greeks = r.get("greeks", {}) or {}
            quote = r.get("last_quote", {}) or {}
            out.append({
                "symbol": symbol.upper(), "type": det.get("contract_type"),
                "expiration": det.get("expiration_date"), "strike": det.get("strike_price"),
                "bid": quote.get("bid"), "ask": quote.get("ask"),
                "volume": (r.get("day") or {}).get("volume"),
                "open_interest": r.get("open_interest"),
                "implied_volatility": r.get("implied_volatility"),
                "delta": greeks.get("delta"), "gamma": greeks.get("gamma"),
                "theta": greeks.get("theta"), "vega": greeks.get("vega"),
                "source": self.name,
            })
        return out
