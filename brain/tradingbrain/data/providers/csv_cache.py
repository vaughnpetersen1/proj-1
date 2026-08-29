"""Local CSV provider -- the offline escape hatch.

Drop files at ``$BRAIN_CACHE_DIR/daily/<SYMBOL>.csv`` (or
``intraday/<TF>/<SYMBOL>.csv``) with a header containing
``date,open,high,low,close,volume`` in any order and this provider serves them.

This is how the operator gets REAL data into the system in an environment where
outbound market-data calls are blocked: export from a broker/TC2000/Norgate and
copy the CSVs in. Data served from here is marked ``DataOrigin.REAL`` and the
provider name records that it came from a local file.
"""

from __future__ import annotations

import csv
import datetime as dt
import pathlib

from ...provenance import DataOrigin
from ..types import Bar, PriceSeries, Timeframe
from .base import HistoricalDataProvider, IntradayDataProvider, ProviderUnavailable

_DATE_KEYS = ("date", "timestamp", "time", "datetime", "t")
_FIELDS = {
    "open": ("open", "o", "adj open", "adj_open"),
    "high": ("high", "h", "adj high", "adj_high"),
    "low": ("low", "l", "adj low", "adj_low"),
    "close": ("close", "c", "adj close", "adj_close", "adjclose"),
    "volume": ("volume", "v", "vol"),
}


def _parse_ts(raw: str) -> dt.datetime:
    raw = raw.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(raw[: len(fmt) + 6], fmt)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"unparseable timestamp {raw!r}") from exc


class CsvCacheProvider(HistoricalDataProvider, IntradayDataProvider):
    name = "csv"
    synthetic = False

    def __init__(self, cache_dir: pathlib.Path) -> None:
        self.cache_dir = pathlib.Path(cache_dir)

    def _dir(self, timeframe: Timeframe) -> pathlib.Path:
        if timeframe is Timeframe.D1:
            return self.cache_dir / "daily"
        return self.cache_dir / "intraday" / timeframe.value

    def available(self) -> tuple[bool, str]:
        d = self._dir(Timeframe.D1)
        files = sorted(d.glob("*.csv")) if d.exists() else []
        if not files:
            return False, (f"No CSVs found in {d}. Drop broker/TC2000 exports there "
                           "to feed the system real data with no network access.")
        return True, f"{len(files)} daily CSV file(s) in {d}"

    def symbols(self, timeframe: Timeframe = Timeframe.D1) -> list[str]:
        d = self._dir(timeframe)
        return sorted(p.stem.upper() for p in d.glob("*.csv")) if d.exists() else []

    def _load(self, symbol: str, timeframe: Timeframe) -> PriceSeries:
        path = self._dir(timeframe) / f"{symbol.upper()}.csv"
        if not path.exists():
            raise ProviderUnavailable(f"no local CSV for {symbol} at {path}")
        bars: list[Bar] = []
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            cols = {(c or "").strip().lower(): c for c in (reader.fieldnames or [])}
            dkey = next((cols[k] for k in _DATE_KEYS if k in cols), None)
            if dkey is None:
                raise ProviderUnavailable(f"{path} has no recognisable date column")
            pick = {}
            for field, aliases in _FIELDS.items():
                key = next((cols[a] for a in aliases if a in cols), None)
                if key is None:
                    raise ProviderUnavailable(f"{path} missing column for {field}")
                pick[field] = key
            for row in reader:
                try:
                    bars.append(Bar(
                        _parse_ts(row[dkey]),
                        float(row[pick["open"]]), float(row[pick["high"]]),
                        float(row[pick["low"]]), float(row[pick["close"]]),
                        float(row[pick["volume"]] or 0.0),
                    ))
                except (ValueError, TypeError):
                    continue
        if not bars:
            raise ProviderUnavailable(f"{path} contained no parseable rows")
        return PriceSeries.from_bars(symbol, timeframe, bars,
                                     provider=f"csv:{path.name}", origin=DataOrigin.REAL)

    def daily(self, symbol, start=None, end=None) -> PriceSeries:
        return self._load(symbol, Timeframe.D1).between(start, end)

    def intraday(self, symbol, timeframe, start=None, end=None) -> PriceSeries:
        return self._load(symbol, timeframe).between(start, end)

    def write(self, series: PriceSeries) -> pathlib.Path:
        d = self._dir(series.timeframe)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{series.symbol.upper()}.csv"
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "open", "high", "low", "close", "volume"])
            for i in range(len(series)):
                w.writerow([series.ts[i].isoformat(), series.open[i], series.high[i],
                            series.low[i], series.close[i], series.volume[i]])
        return path
