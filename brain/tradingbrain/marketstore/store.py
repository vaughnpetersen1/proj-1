"""Persistent market-data store.

The database is the source of truth for market data. Engines read from here;
they do not call a vendor. The ingestion service is the only thing that talks to
a provider, and every row it writes records which provider and feed produced it.

Two invariants this class enforces rather than documents:

1. **Raw is never overwritten by derived.** There is no method here that writes
   a computed value into a ``market_data_*`` table. Indicators and features go to
   ``technical_indicators`` / ``strategy_features``, which are droppable.

2. **Datasets are never silently mixed.** ``upsert_bars`` refuses to overwrite a
   bar that a different provider supplied unless the caller explicitly allows it,
   and records the conflict. A backtest whose bars came half from Alpaca IEX and
   half from a CSV export is not a backtest of anything.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import threading
from typing import Any, Iterable, Sequence

import numpy as np

from ..config import SETTINGS, Settings
from ..data.types import Bar, PriceSeries, Timeframe
from ..provenance import DataOrigin
from . import calendar as cal

SCHEMA = pathlib.Path(__file__).with_name("schema.sql")

TIMEFRAME_TABLE: dict[str, str] = {
    "1d": "market_data_daily",
    "1m": "market_data_1m",
    "5m": "market_data_5m",
    "15m": "market_data_15m",
    "1h": "market_data_1h",
}

BAR_COLUMNS = ("symbol_id", "ts", "open", "high", "low", "close", "volume",
               "trade_count", "vwap", "provider", "feed", "adjusted", "ingested_at")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _epoch(ts: dt.datetime) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return int(ts.timestamp())


def _dt(epoch: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).replace(tzinfo=None)


def table_for(timeframe: str | Timeframe) -> str:
    key = timeframe.value if isinstance(timeframe, Timeframe) else str(timeframe)
    if key not in TIMEFRAME_TABLE:
        raise KeyError(f"no market-data table for timeframe {key!r}; "
                       f"known: {sorted(TIMEFRAME_TABLE)}")
    return TIMEFRAME_TABLE[key]


class MarketStore:
    def __init__(self, path: pathlib.Path | str | None = None,
                 settings: Settings = SETTINGS) -> None:
        self.path = pathlib.Path(path or getattr(settings, "market_db_path",
                                                 settings.cache_dir / "market.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self.init_schema()

    # -- plumbing ----------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, check_same_thread=False, timeout=60)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = c
        return c

    #: Additive column migrations, applied on open. See db/store.py for why.
    MIGRATIONS: tuple[tuple[str, str, str], ...] = ()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA.read_text())
        self.conn.commit()
        for table, column, decl in self.MIGRATIONS:
            try:
                cols = {r["name"] for r in self.query(f"PRAGMA table_info({table})")}
            except sqlite3.OperationalError:
                continue
            if cols and column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                self.conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._write_lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> int:
        if not rows:
            return 0
        with self._write_lock:
            cur = self.conn.executemany(sql, rows)
            self.conn.commit()
            return cur.rowcount

    # -- kv / log ----------------------------------------------------------
    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                     " updated_at=excluded.updated_at",
                     (key, json.dumps(value), _now()))

    def log(self, level: str, kind: str, detail: str, symbol: str | None = None,
            timeframe: str | None = None) -> None:
        self.execute("INSERT INTO ingest_log(ts,level,kind,symbol,timeframe,detail) "
                     "VALUES(?,?,?,?,?,?)", (_now(), level, kind, symbol, timeframe, detail))

    def logs(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ingest_log"
        params: list[Any] = []
        if level:
            sql += " WHERE level=?"
            params.append(level)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def record_api_call(self, provider: str, endpoint: str, *, symbols: int = 1,
                        rows: int = 0, ok: bool = True, ms: float | None = None,
                        error: str | None = None) -> None:
        self.execute("INSERT INTO api_usage(ts,provider,endpoint,symbols,rows,ok,ms,error) "
                     "VALUES(?,?,?,?,?,?,?,?)",
                     (_now(), provider, endpoint, symbols, rows, int(ok), ms, error))

    def api_usage(self, since_hours: int = 24) -> dict[str, Any]:
        cutoff = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(hours=since_hours)).isoformat(timespec="seconds")
        rows = self.query(
            "SELECT provider, endpoint, COUNT(*) n, SUM(rows) rows, SUM(1-ok) errors, "
            "AVG(ms) avg_ms FROM api_usage WHERE ts>=? GROUP BY provider, endpoint "
            "ORDER BY n DESC", (cutoff,))
        total = self.one("SELECT COUNT(*) n, SUM(1-ok) errors FROM api_usage WHERE ts>=?",
                         (cutoff,)) or {}
        recent_errors = self.query(
            "SELECT ts, provider, endpoint, error FROM api_usage "
            "WHERE ok=0 AND ts>=? ORDER BY id DESC LIMIT 20", (cutoff,))
        return {"window_hours": since_hours, "calls": total.get("n") or 0,
                "errors": total.get("errors") or 0, "by_endpoint": rows,
                "recent_errors": recent_errors}

    # -- symbols -----------------------------------------------------------
    def symbol_id(self, symbol: str, create: bool = True,
                  **fields: Any) -> int | None:
        sym = symbol.upper().strip()
        row = self.one("SELECT id FROM symbols WHERE symbol=?", (sym,))
        if row:
            if fields:
                self.execute(
                    "UPDATE symbols SET last_seen=? WHERE id=?", (_now(), row["id"]))
            return int(row["id"])
        if not create:
            return None
        now = _now()
        cur = self.execute(
            "INSERT INTO symbols(symbol,name,exchange,asset_class,sector,industry,active,"
            "tradable,shortable,easy_to_borrow,ipo_date,delisted_date,source,first_seen,"
            "last_seen,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sym, fields.get("name"), fields.get("exchange"),
             fields.get("asset_class", "us_equity"), fields.get("sector"),
             fields.get("industry"), int(fields.get("active", 1)),
             fields.get("tradable"), fields.get("shortable"),
             fields.get("easy_to_borrow"), fields.get("ipo_date"),
             fields.get("delisted_date"), fields.get("source"), now, now,
             fields.get("note")))
        return int(cur.lastrowid or 0)

    def upsert_symbols(self, rows: Iterable[dict[str, Any]], source: str) -> dict[str, Any]:
        added = updated = 0
        for r in rows:
            sym = str(r.get("symbol", "")).upper().strip()
            if not sym:
                continue
            existing = self.one("SELECT id FROM symbols WHERE symbol=?", (sym,))
            now = _now()
            if existing:
                self.execute(
                    "UPDATE symbols SET name=COALESCE(?,name), exchange=COALESCE(?,exchange),"
                    " asset_class=COALESCE(?,asset_class), sector=COALESCE(?,sector),"
                    " industry=COALESCE(?,industry), active=?, tradable=COALESCE(?,tradable),"
                    " shortable=COALESCE(?,shortable), easy_to_borrow=COALESCE(?,easy_to_borrow),"
                    " ipo_date=COALESCE(?,ipo_date), delisted_date=COALESCE(?,delisted_date),"
                    " source=?, last_seen=? WHERE id=?",
                    (r.get("name"), r.get("exchange"), r.get("asset_class"), r.get("sector"),
                     r.get("industry"), int(r.get("active", 1)), r.get("tradable"),
                     r.get("shortable"), r.get("easy_to_borrow"), r.get("ipo_date"),
                     r.get("delisted_date"), source, now, existing["id"]))
                updated += 1
            else:
                self.symbol_id(sym, create=True, source=source, **{
                    k: v for k, v in r.items() if k != "symbol"})
                added += 1
        self.kv_set("universe_last_sync", {"at": _now(), "source": source,
                                           "added": added, "updated": updated})
        return {"added": added, "updated": updated,
                "total": self.one("SELECT COUNT(*) n FROM symbols")["n"]}

    def symbols(self, active_only: bool = True, limit: int | None = None,
                sector: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM symbols WHERE 1=1"
        params: list[Any] = []
        if active_only:
            sql += " AND active=1"
        if sector:
            sql += " AND sector=?"
            params.append(sector)
        sql += " ORDER BY symbol"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self.query(sql, params)

    def symbol_list(self, active_only: bool = True) -> list[str]:
        return [r["symbol"] for r in self.query(
            "SELECT symbol FROM symbols" + (" WHERE active=1" if active_only else "")
            + " ORDER BY symbol")]

    def sector_map(self) -> dict[str, str]:
        return {r["symbol"]: r["sector"] for r in self.query(
            "SELECT symbol, sector FROM symbols WHERE sector IS NOT NULL")}

    def deactivate_symbol(self, symbol: str, delisted_date: str | None = None) -> None:
        self.execute("UPDATE symbols SET active=0, delisted_date=COALESCE(?,delisted_date),"
                     " last_seen=? WHERE symbol=?",
                     (delisted_date, _now(), symbol.upper()))

    # -- RAW bars ----------------------------------------------------------
    def upsert_bars(self, symbol: str, timeframe: str | Timeframe,
                    bars: Sequence[Bar] | PriceSeries, provider: str,
                    feed: str | None = None, adjusted: bool = True,
                    allow_provider_override: bool = False) -> dict[str, Any]:
        """Write raw bars. Returns counts including provider conflicts.

        A bar already present from a *different* provider is left alone unless
        ``allow_provider_override`` is set, and the conflict is counted and
        logged. Mixing vendors inside one series silently is how a backtest ends
        up measuring a data seam instead of a strategy.
        """
        table = table_for(timeframe)
        tf = timeframe.value if isinstance(timeframe, Timeframe) else str(timeframe)
        sid = self.symbol_id(symbol)
        seq = list(bars) if not isinstance(bars, PriceSeries) else list(bars)
        if not seq:
            return {"inserted": 0, "updated": 0, "conflicts": 0, "rows": 0}

        existing = {r["ts"]: r["provider"] for r in self.query(
            f"SELECT ts, provider FROM {table} WHERE symbol_id=? AND ts BETWEEN ? AND ?",
            (sid, _epoch(seq[0].ts), _epoch(seq[-1].ts)))}

        now = _now()
        to_write: list[tuple] = []
        conflicts = 0
        updated = 0
        for b in seq:
            e = _epoch(b.ts)
            prev = existing.get(e)
            if prev is not None:
                if prev != provider and not allow_provider_override:
                    conflicts += 1
                    continue
                updated += 1
            to_write.append((sid, e, float(b.open), float(b.high), float(b.low),
                             float(b.close), float(b.volume),
                             getattr(b, "trade_count", None), getattr(b, "vwap", None),
                             provider, feed, int(bool(adjusted)), now))
        placeholders = ",".join("?" * len(BAR_COLUMNS))
        self.executemany(
            f"INSERT INTO {table} ({','.join(BAR_COLUMNS)}) VALUES ({placeholders}) "
            "ON CONFLICT(symbol_id, ts) DO UPDATE SET open=excluded.open,"
            " high=excluded.high, low=excluded.low, close=excluded.close,"
            " volume=excluded.volume, trade_count=excluded.trade_count,"
            " vwap=excluded.vwap, provider=excluded.provider, feed=excluded.feed,"
            " adjusted=excluded.adjusted, ingested_at=excluded.ingested_at",
            to_write)
        if conflicts:
            self.log("warning", "ingest",
                     f"{conflicts} bar(s) skipped: already present from a different "
                     f"provider. Pass allow_provider_override to replace them.",
                     symbol.upper(), tf)
        if to_write:
            # Only a provider that actually wrote rows may claim the sync status;
            # otherwise a fully-rejected import would relabel the whole series.
            self._refresh_sync_status(sid, tf, provider, feed)
        return {"inserted": len(to_write) - updated, "updated": updated,
                "conflicts": conflicts, "rows": len(to_write)}

    def bars(self, symbol: str, timeframe: str | Timeframe = "1d",
             start: dt.date | dt.datetime | None = None,
             end: dt.date | dt.datetime | None = None,
             limit: int | None = None) -> PriceSeries:
        table = table_for(timeframe)
        tf = Timeframe(timeframe.value if isinstance(timeframe, Timeframe) else timeframe)
        sid = self.symbol_id(symbol, create=False)
        empty = PriceSeries(symbol.upper(), tf, [], *(np.array([]) for _ in range(5)),
                            provider="marketstore", origin=DataOrigin.UNKNOWN)
        if sid is None:
            return empty
        sql = f"SELECT * FROM {table} WHERE symbol_id=?"
        params: list[Any] = [sid]
        if start is not None:
            sql += " AND ts>=?"
            params.append(_epoch(_as_dt(start, end_of_day=False)))
        if end is not None:
            sql += " AND ts<=?"
            params.append(_epoch(_as_dt(end, end_of_day=True)))
        sql += " ORDER BY ts"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.query(sql, params)
        if not rows:
            return empty
        providers = {r["provider"] for r in rows}
        feeds = {r["feed"] for r in rows}
        provider_label = (next(iter(providers)) if len(providers) == 1
                          else "MIXED:" + ",".join(sorted(providers)))
        origin = (DataOrigin.SYNTHETIC if any("synthetic" in p for p in providers)
                  else DataOrigin.REAL)
        series = PriceSeries(
            symbol=symbol.upper(), timeframe=tf,
            ts=[_dt(r["ts"]) for r in rows],
            open=np.array([r["open"] for r in rows], float),
            high=np.array([r["high"] for r in rows], float),
            low=np.array([r["low"] for r in rows], float),
            close=np.array([r["close"] for r in rows], float),
            volume=np.array([r["volume"] for r in rows], float),
            provider=provider_label,
            origin=origin,
            adjusted=all(r["adjusted"] for r in rows),
        )
        if len(providers) > 1:
            series.warnings.append(
                f"Bars in this range came from multiple providers ({sorted(providers)}). "
                "Statistics over a provider seam are not comparable.")
        feed = next(iter(feeds)) if len(feeds) == 1 else None
        if feed:
            series.warnings.append(f"feed={feed}")
        return series

    def coverage(self, symbol: str, timeframe: str | Timeframe = "1d") -> dict[str, Any]:
        table = table_for(timeframe)
        sid = self.symbol_id(symbol, create=False)
        if sid is None:
            return {"symbol": symbol.upper(), "rows": 0, "first": None, "last": None}
        r = self.one(f"SELECT COUNT(*) n, MIN(ts) lo, MAX(ts) hi FROM {table} "
                     "WHERE symbol_id=?", (sid,)) or {}
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe.value if isinstance(timeframe, Timeframe) else timeframe,
            "rows": r.get("n") or 0,
            "first": _dt(r["lo"]).date().isoformat() if r.get("lo") else None,
            "last": _dt(r["hi"]).date().isoformat() if r.get("hi") else None,
        }

    def missing_daily_ranges(self, symbol: str, want_start: dt.date,
                             want_end: dt.date) -> list[tuple[dt.date, dt.date]]:
        """Trading days in [want_start, want_end] that the store does not hold.

        Uses the exchange calendar, so weekends and holidays are not reported as
        gaps. Contiguous missing days are merged into ranges the ingestion
        service can request in one call -- the difference between one API request
        and two hundred.
        """
        sid = self.symbol_id(symbol, create=False)
        have: set[dt.date] = set()
        if sid is not None:
            have = {_dt(r["ts"]).date() for r in self.query(
                "SELECT ts FROM market_data_daily WHERE symbol_id=? AND ts BETWEEN ? AND ?",
                (sid, _epoch(dt.datetime.combine(want_start, dt.time.min)),
                 _epoch(dt.datetime.combine(want_end, dt.time.max))))}
        missing = [d for d in cal.trading_days(want_start, want_end) if d not in have]
        if not missing:
            return []
        ranges: list[tuple[dt.date, dt.date]] = []
        run_start = prev = missing[0]
        for d in missing[1:]:
            if cal.next_trading_day(prev) == d:
                prev = d
                continue
            ranges.append((run_start, prev))
            run_start = prev = d
        ranges.append((run_start, prev))
        return ranges

    def delete_bars(self, symbol: str, timeframe: str | Timeframe = "1d") -> int:
        table = table_for(timeframe)
        sid = self.symbol_id(symbol, create=False)
        if sid is None:
            return 0
        cur = self.execute(f"DELETE FROM {table} WHERE symbol_id=?", (sid,))
        tf = timeframe.value if isinstance(timeframe, Timeframe) else timeframe
        self.execute("DELETE FROM data_sync_status WHERE symbol_id=? AND timeframe=?",
                     (sid, tf))
        return cur.rowcount

    # -- sync status -------------------------------------------------------
    def _refresh_sync_status(self, sid: int, timeframe: str, provider: str,
                             feed: str | None, status: str = "ok",
                             error: str | None = None) -> None:
        table = TIMEFRAME_TABLE[timeframe]
        r = self.one(f"SELECT COUNT(*) n, MIN(ts) lo, MAX(ts) hi FROM {table} "
                     "WHERE symbol_id=?", (sid,)) or {}
        now = _now()
        self.execute(
            "INSERT INTO data_sync_status(symbol_id,timeframe,provider,feed,first_ts,last_ts,"
            "rows,last_successful_sync,last_attempt,status,error) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol_id,timeframe) DO UPDATE SET provider=excluded.provider,"
            " feed=excluded.feed, first_ts=excluded.first_ts, last_ts=excluded.last_ts,"
            " rows=excluded.rows, last_successful_sync=excluded.last_successful_sync,"
            " last_attempt=excluded.last_attempt, status=excluded.status, error=excluded.error",
            (sid, timeframe, provider, feed, r.get("lo"), r.get("hi"), r.get("n") or 0,
             now if status == "ok" else None, now, status, error))

    def mark_sync_error(self, symbol: str, timeframe: str, error: str,
                        provider: str | None = None) -> None:
        sid = self.symbol_id(symbol)
        self.execute(
            "INSERT INTO data_sync_status(symbol_id,timeframe,provider,last_attempt,status,error)"
            " VALUES(?,?,?,?,'error',?) "
            "ON CONFLICT(symbol_id,timeframe) DO UPDATE SET last_attempt=excluded.last_attempt,"
            " status='error', error=excluded.error, provider=COALESCE(excluded.provider,provider)",
            (sid, timeframe, provider, _now(), error))

    def sync_status(self, timeframe: str | None = None,
                    limit: int = 500) -> list[dict[str, Any]]:
        sql = ("SELECT s.symbol, d.* FROM data_sync_status d "
               "JOIN symbols s ON s.id=d.symbol_id WHERE 1=1")
        params: list[Any] = []
        if timeframe:
            sql += " AND d.timeframe=?"
            params.append(timeframe)
        sql += " ORDER BY d.last_attempt DESC LIMIT ?"
        params.append(limit)
        rows = self.query(sql, params)
        for r in rows:
            r["first"] = _dt(r["first_ts"]).date().isoformat() if r.get("first_ts") else None
            r["last"] = _dt(r["last_ts"]).date().isoformat() if r.get("last_ts") else None
        return rows

    def freshness(self, timeframe: str = "1d") -> dict[str, Any]:
        """What the UI's data-status indicator reads. Never guesses."""
        row = self.one(
            "SELECT MAX(last_ts) hi, MAX(last_successful_sync) sync, COUNT(*) n "
            "FROM data_sync_status WHERE timeframe=?", (timeframe,)) or {}
        providers = self.query(
            "SELECT provider, feed, COUNT(*) n FROM data_sync_status WHERE timeframe=? "
            "AND provider IS NOT NULL GROUP BY provider, feed ORDER BY n DESC", (timeframe,))
        last_bar = _dt(row["hi"]) if row.get("hi") else None
        age_min = None
        if last_bar is not None:
            age_min = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                       - last_bar).total_seconds() / 60.0
        stream = self.kv_get("realtime_status", {}) or {}
        return {
            "timeframe": timeframe,
            "symbols_tracked": row.get("n") or 0,
            "last_bar_ts": last_bar.isoformat() if last_bar else None,
            "last_bar_age_minutes": round(age_min, 1) if age_min is not None else None,
            "last_successful_sync": row.get("sync"),
            "providers": providers,
            "primary_provider": providers[0]["provider"] if providers else None,
            "primary_feed": providers[0]["feed"] if providers else None,
            "realtime": stream,
        }

    # -- corporate actions / options --------------------------------------
    def upsert_corporate_actions(self, symbol: str, actions: Iterable[dict[str, Any]],
                                 provider: str) -> int:
        sid = self.symbol_id(symbol)
        rows = []
        for a in actions:
            key = "|".join(str(a.get(k, "")) for k in
                           ("ex_date", "action_type", "ratio", "cash_amount"))
            rows.append((sid, a.get("ex_date"), a.get("action_type"), a.get("ratio"),
                         a.get("cash_amount"), a.get("record_date"), a.get("payable_date"),
                         provider, json.dumps(a.get("raw", a)), _now(),
                         f"{sid}|{key}"))
        return self.executemany(
            "INSERT OR IGNORE INTO corporate_actions(symbol_id,ex_date,action_type,ratio,"
            "cash_amount,record_date,payable_date,provider,raw,ingested_at,dedupe_key) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)

    def corporate_actions(self, symbol: str, start: dt.date | None = None,
                          end: dt.date | None = None) -> list[dict[str, Any]]:
        sid = self.symbol_id(symbol, create=False)
        if sid is None:
            return []
        sql = "SELECT * FROM corporate_actions WHERE symbol_id=?"
        params: list[Any] = [sid]
        if start:
            sql += " AND ex_date>=?"
            params.append(start.isoformat())
        if end:
            sql += " AND ex_date<=?"
            params.append(end.isoformat())
        return self.query(sql + " ORDER BY ex_date", params)

    def upsert_options(self, underlying: str, contracts: Iterable[dict[str, Any]],
                       provider: str, feed: str | None, as_of: str) -> int:
        uid = self.symbol_id(underlying)
        rows = [(uid, c.get("contract") or c.get("symbol"), c.get("expiration"),
                 c.get("strike"), c.get("type") or c.get("option_type"), c.get("style"),
                 c.get("open_interest"), c.get("bid"), c.get("ask"), c.get("last"),
                 c.get("volume"), c.get("implied_volatility"), c.get("delta"),
                 c.get("gamma"), c.get("theta"), c.get("vega"), c.get("rho"),
                 as_of, provider, feed) for c in contracts]
        return self.executemany(
            "INSERT OR REPLACE INTO options_contracts(underlying_id,contract,expiration,"
            "strike,option_type,style,open_interest,bid,ask,last,volume,implied_volatility,"
            "delta,gamma,theta,vega,rho,as_of,provider,feed) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def options_chain(self, underlying: str, expiration: str | None = None,
                      as_of: str | None = None) -> list[dict[str, Any]]:
        uid = self.symbol_id(underlying, create=False)
        if uid is None:
            return []
        if as_of is None:
            r = self.one("SELECT MAX(as_of) a FROM options_contracts WHERE underlying_id=?",
                         (uid,))
            as_of = (r or {}).get("a")
        if as_of is None:
            return []
        sql = "SELECT * FROM options_contracts WHERE underlying_id=? AND as_of=?"
        params: list[Any] = [uid, as_of]
        if expiration:
            sql += " AND expiration=?"
            params.append(expiration)
        return self.query(sql + " ORDER BY expiration, strike, option_type", params)

    # -- DERIVED -----------------------------------------------------------
    def upsert_indicators(self, symbol: str, rows: Sequence[dict[str, Any]],
                          timeframe: str = "1d", feature_version: int = 1) -> int:
        sid = self.symbol_id(symbol)
        cols = ["sma10", "sma20", "sma50", "sma200", "ema21", "rsi14", "atr14", "adr20",
                "macd", "macd_signal", "macd_hist", "vwap", "relative_volume",
                "dollar_volume_20d", "r2_20"]
        now = _now()
        payload = [tuple([sid, r["ts"], timeframe]
                         + [_clean(r.get(c)) for c in cols] + [now, feature_version])
                   for r in rows]
        return self.executemany(
            f"INSERT OR REPLACE INTO technical_indicators(symbol_id,ts,timeframe,"
            f"{','.join(cols)},computed_at,feature_version) VALUES "
            f"({','.join('?' * (len(cols) + 5))})", payload)

    def upsert_strategy_features(self, symbol: str, rows: Sequence[dict[str, Any]],
                                 timeframe: str = "1d", feature_version: int = 1,
                                 config_hash: str | None = None) -> int:
        sid = self.symbol_id(symbol)
        cols = STRATEGY_FEATURE_COLUMNS
        now = _now()
        payload = [tuple([sid, r["ts"], timeframe]
                         + [_clean(r.get(c)) for c in cols] + [now, feature_version,
                                                               config_hash])
                   for r in rows]
        return self.executemany(
            f"INSERT OR REPLACE INTO strategy_features(symbol_id,ts,timeframe,"
            f"{','.join(cols)},computed_at,feature_version,config_hash) VALUES "
            f"({','.join('?' * (len(cols) + 6))})", payload)

    def features_at(self, as_of: dt.date, timeframe: str = "1d",
                    symbols: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """Latest feature row at or before ``as_of`` for each symbol."""
        ts = _epoch(dt.datetime.combine(as_of, dt.time.max))
        sql = ("SELECT s.symbol, f.* FROM strategy_features f "
               "JOIN symbols s ON s.id=f.symbol_id "
               "JOIN (SELECT symbol_id, MAX(ts) mts FROM strategy_features "
               "      WHERE timeframe=? AND ts<=? GROUP BY symbol_id) m "
               "  ON m.symbol_id=f.symbol_id AND m.mts=f.ts "
               "WHERE f.timeframe=?")
        params: list[Any] = [timeframe, ts, timeframe]
        if symbols:
            marks = ",".join("?" * len(symbols))
            sql += f" AND s.symbol IN ({marks})"
            params += [s.upper() for s in symbols]
        rows = self.query(sql, params)
        for r in rows:
            r["date"] = _dt(r["ts"]).date().isoformat()
        return rows

    def feature_coverage(self, timeframe: str = "1d") -> dict[str, Any]:
        r = self.one("SELECT COUNT(*) rows, COUNT(DISTINCT symbol_id) symbols, "
                     "MIN(ts) lo, MAX(ts) hi, MAX(computed_at) computed "
                     "FROM strategy_features WHERE timeframe=?", (timeframe,)) or {}
        return {"rows": r.get("rows") or 0, "symbols": r.get("symbols") or 0,
                "first": _dt(r["lo"]).date().isoformat() if r.get("lo") else None,
                "last": _dt(r["hi"]).date().isoformat() if r.get("hi") else None,
                "last_computed_at": r.get("computed")}

    def clear_derived(self, what: str = "all") -> dict[str, int]:
        """Derived tables are always safe to drop -- they are recomputable."""
        targets = {"indicators": "technical_indicators", "features": "strategy_features",
                   "regimes": "market_regimes", "sectors": "sector_data",
                   "scans": "scanner_runs"}
        out = {}
        for key, table in targets.items():
            if what in ("all", key):
                out[table] = self.execute(f"DELETE FROM {table}").rowcount
        return out

    def save_regime(self, as_of: str, payload: dict[str, Any],
                    index_set: str = "SPY,QQQ") -> None:
        b = payload.get("breadth") or {}
        self.execute(
            "INSERT OR REPLACE INTO market_regimes(as_of,index_set,label,volatility_label,"
            "strength,filter_pass,breadth_above_50sma,breadth_above_200sma,components,"
            "provider,feed,origin,computed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (as_of, index_set, payload.get("label"), payload.get("volatility_label"),
             payload.get("strength"), int(bool(payload.get("filter_pass"))),
             b.get("pct_above_50sma"), b.get("pct_above_200sma"),
             json.dumps(payload.get("components") or {}), payload.get("data_provider"),
             payload.get("feed"), payload.get("data_origin"), _now()))

    def regime_history(self, limit: int = 400) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM market_regimes ORDER BY as_of DESC LIMIT ?", (limit,))

    def save_sector_ranks(self, as_of: str, kind: str, ranks: Sequence[dict[str, Any]]) -> int:
        now = _now()
        return self.executemany(
            "INSERT OR REPLACE INTO sector_data(as_of,kind,name,rank,score,members,"
            "components,leaders,computed_at) VALUES(?,?,?,?,?,?,?,?,?)",
            [(as_of, kind, g["name"], g["rank"], g.get("score"),
              g.get("members_with_data"), json.dumps(g.get("components") or {}),
              json.dumps(g.get("leaders") or []), now) for g in ranks])

    def sector_history(self, kind: str = "sector", limit: int = 200) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM sector_data WHERE kind=? ORDER BY as_of DESC, rank "
                          "LIMIT ?", (kind, limit))

    def save_scan(self, as_of: str, config: dict[str, Any], results: Sequence[dict[str, Any]],
                  *, strategy: str | None = None, engine: str = "sql",
                  symbols_scanned: int = 0, data_origin: str | None = None,
                  provider: str | None = None, feed: str | None = None,
                  runtime_ms: float | None = None) -> int:
        cfg = json.dumps(config, sort_keys=True, default=str)
        cur = self.execute(
            "INSERT INTO scanner_runs(as_of,strategy,config,config_hash,engine,"
            "symbols_scanned,matched,data_origin,provider,feed,runtime_ms,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (as_of, strategy, cfg, hashlib.sha256(cfg.encode()).hexdigest()[:16], engine,
             symbols_scanned, len(results), data_origin, provider, feed, runtime_ms, _now()))
        run_id = int(cur.lastrowid or 0)
        self.executemany(
            "INSERT OR REPLACE INTO scanner_results(run_id,rank,symbol,score,state,sector,"
            "payload) VALUES(?,?,?,?,?,?,?)",
            [(run_id, i + 1, r.get("symbol"), r.get("score"), r.get("state"),
              r.get("sector"), json.dumps(r, default=str)) for i, r in enumerate(results)])
        return run_id

    def latest_scan(self) -> dict[str, Any] | None:
        run = self.one("SELECT * FROM scanner_runs ORDER BY id DESC LIMIT 1")
        if not run:
            return None
        run["config"] = json.loads(run["config"])
        run["results"] = [json.loads(r["payload"]) for r in self.query(
            "SELECT payload FROM scanner_results WHERE run_id=? ORDER BY rank", (run["id"],))]
        return run

    # -- data quality ------------------------------------------------------
    def flag(self, symbol: str, timeframe: str, code: str, severity: str,
             detail: str, sample: Any = None) -> None:
        self.execute(
            "INSERT INTO data_quality_flags(ts,symbol,timeframe,code,severity,detail,sample) "
            "VALUES(?,?,?,?,?,?,?)",
            (_now(), symbol.upper(), timeframe, code, severity, detail,
             json.dumps(sample) if sample is not None else None))

    def flags(self, unresolved_only: bool = True, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM data_quality_flags"
        if unresolved_only:
            sql += " WHERE resolved=0"
        return self.query(sql + " ORDER BY id DESC LIMIT ?", (limit,))

    def resolve_flags(self, symbol: str | None = None) -> int:
        if symbol:
            return self.execute("UPDATE data_quality_flags SET resolved=1 WHERE symbol=?",
                                (symbol.upper(),)).rowcount
        return self.execute("UPDATE data_quality_flags SET resolved=1").rowcount

    # -- dataset versioning ------------------------------------------------
    def lock_dataset(self, *, symbols: Sequence[str], timeframe: str,
                     start: dt.date | None, end: dt.date | None, provider: str,
                     feed: str | None, origin: str, adjustment: str,
                     label: str | None = None, note: str | None = None) -> dict[str, Any]:
        """Freeze the exact bars a backtest is about to use.

        The checksum is over (symbol, ts, ohlcv) for every bar in scope, so any
        later re-adjustment, backfill or provider change produces a different
        dataset id and the old result stays attributable to the old data.
        """
        h = hashlib.sha256()
        rows = 0
        first_ts: int | None = None
        last_ts: int | None = None
        for sym in sorted({s.upper() for s in symbols}):
            sid = self.symbol_id(sym, create=False)
            if sid is None:
                continue
            table = table_for(timeframe)
            sql = f"SELECT ts,open,high,low,close,volume FROM {table} WHERE symbol_id=?"
            params: list[Any] = [sid]
            if start is not None:
                sql += " AND ts>=?"
                params.append(_epoch(dt.datetime.combine(start, dt.time.min)))
            if end is not None:
                sql += " AND ts<=?"
                params.append(_epoch(dt.datetime.combine(end, dt.time.max)))
            for r in self.conn.execute(sql + " ORDER BY ts", params):
                rows += 1
                first_ts = r[0] if first_ts is None else min(first_ts, r[0])
                last_ts = r[0] if last_ts is None else max(last_ts, r[0])
                h.update(f"{sym}|{r[0]}|{r[1]:.6f}|{r[2]:.6f}|{r[3]:.6f}|"
                         f"{r[4]:.6f}|{r[5]:.2f}".encode())
        universe = sorted({s.upper() for s in symbols})
        uhash = hashlib.sha256("|".join(universe).encode()).hexdigest()[:16]
        checksum = h.hexdigest()[:32]
        existing = self.one("SELECT * FROM dataset_versions WHERE checksum=?", (checksum,))
        if existing:
            existing["universe"] = json.loads(existing["universe"])
            existing["reused"] = True
            return existing
        cur = self.execute(
            "INSERT INTO dataset_versions(created_at,label,provider,feed,origin,timeframe,"
            "start_ts,end_ts,symbol_count,row_count,universe_hash,universe,adjustment,"
            "checksum,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), label, provider, feed, origin, timeframe, first_ts, last_ts,
             len(universe), rows, uhash, json.dumps(universe), adjustment, checksum, note))
        out = self.one("SELECT * FROM dataset_versions WHERE id=?", (cur.lastrowid,)) or {}
        out["universe"] = json.loads(out["universe"])
        out["reused"] = False
        return out

    def dataset_version(self, ds_id: int) -> dict[str, Any] | None:
        r = self.one("SELECT * FROM dataset_versions WHERE id=?", (ds_id,))
        if r:
            r["universe"] = json.loads(r["universe"])
            r["start"] = _dt(r["start_ts"]).date().isoformat() if r.get("start_ts") else None
            r["end"] = _dt(r["end_ts"]).date().isoformat() if r.get("end_ts") else None
        return r

    def dataset_versions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.query("SELECT id,created_at,label,provider,feed,origin,timeframe,"
                          "symbol_count,row_count,adjustment,checksum FROM dataset_versions "
                          "ORDER BY id DESC LIMIT ?", (limit,))
        return rows

    # -- housekeeping ------------------------------------------------------
    def prune_raw_ticks(self, keep_days: int) -> dict[str, int]:
        cutoff = _epoch(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)) * 1_000_000
        t = self.execute("DELETE FROM raw_trades WHERE ts < ?", (cutoff,)).rowcount
        q = self.execute("DELETE FROM raw_quotes WHERE ts < ?", (cutoff,)).rowcount
        return {"trades_deleted": t, "quotes_deleted": q}

    def vacuum(self) -> None:
        with self._write_lock:
            self.conn.execute("VACUUM")

    def stats(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for t in ("symbols", "market_data_daily", "market_data_1m", "market_data_5m",
                  "market_data_15m", "market_data_1h", "raw_trades", "raw_quotes",
                  "corporate_actions", "options_contracts", "technical_indicators",
                  "strategy_features", "market_regimes", "sector_data",
                  "scanner_runs", "scanner_results", "dataset_versions",
                  "data_quality_flags"):
            counts[t] = (self.one(f"SELECT COUNT(*) n FROM {t}") or {}).get("n", 0)
        size = 0
        for suffix in ("", "-wal", "-shm"):
            p = pathlib.Path(str(self.path) + suffix)
            if p.exists():
                size += p.stat().st_size
        origins = self.query(
            "SELECT provider, feed, COUNT(*) rows, COUNT(DISTINCT symbol_id) symbols "
            "FROM market_data_daily GROUP BY provider, feed ORDER BY rows DESC")
        return {
            "path": str(self.path),
            "size_bytes": size,
            "size_mb": round(size / 1_048_576, 2),
            "row_counts": counts,
            "daily_bars_by_provider": origins,
            "active_symbols": (self.one("SELECT COUNT(*) n FROM symbols WHERE active=1")
                               or {}).get("n", 0),
            "unresolved_quality_flags": (
                self.one("SELECT COUNT(*) n FROM data_quality_flags WHERE resolved=0")
                or {}).get("n", 0),
        }


STRATEGY_FEATURE_COLUMNS = [
    "close", "volume",
    "prior_5d_return", "prior_10d_return", "prior_20d_return", "prior_60d_return",
    "prior_120d_return", "prior_move_pct", "prior_move_bars",
    "distance_from_10sma", "distance_from_20sma", "distance_from_50sma",
    "distance_from_200sma", "above_10sma", "above_20sma", "above_50sma",
    "above_200sma", "sma_stack_10_20_50",
    "consolidation_days", "consolidation_high", "consolidation_low",
    "consolidation_depth_pct", "range_contraction", "volume_contraction",
    "higher_low_ratio", "base_quality", "base_qualifies",
    "breakout_distance_pct", "breakout_today", "breakout_volume_ratio",
    "close_position", "pct_from_52w_high", "relative_volume", "adr20", "atr14",
    "dollar_volume_20d", "market_relative_strength", "relative_strength_pct",
    "sector_relative_strength",
]


def _clean(v: Any) -> Any:
    """NaN/inf never reach the database; a missing feature is NULL, not 'nan'."""
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return int(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return f if np.isfinite(f) else None
    return v


def _as_dt(v: dt.date | dt.datetime, end_of_day: bool) -> dt.datetime:
    if isinstance(v, dt.datetime):
        return v
    return dt.datetime.combine(v, dt.time.max if end_of_day else dt.time.min)


MARKET = MarketStore()
