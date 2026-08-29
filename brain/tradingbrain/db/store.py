"""Thin SQLite persistence layer.

Deliberately not an ORM: the research artefacts are JSON documents with a few
indexed columns, and keeping the SQL visible makes the reproducibility story
auditable.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
import threading
from typing import Any, Iterable, Sequence

from ..config import SETTINGS, Settings

SCHEMA = pathlib.Path(__file__).with_name("schema.sql")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: pathlib.Path | str | None = None,
                 settings: Settings = SETTINGS) -> None:
        self.path = pathlib.Path(path or settings.db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.init_schema()

    # -- plumbing ----------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            self._local.conn = c
        return c

    #: Columns added after the first release. `CREATE TABLE IF NOT EXISTS` will not
    #: add a column to a database that already exists, so new ones are applied
    #: here. Additive only: nothing is dropped or retyped, so an older database
    #: keeps working and a rollback stays possible.
    MIGRATIONS: tuple[tuple[str, str, str], ...] = (
        ("backtests", "dataset_version_id", "INTEGER"),
        ("backtests", "provenance", "TEXT"),
    )

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA.read_text())
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        for table, column, decl in self.MIGRATIONS:
            try:
                cols = {r["name"] for r in self.query(f"PRAGMA table_info({table})")}
            except sqlite3.OperationalError:
                continue                       # table not created yet on a fresh file
            if cols and column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                self.conn.commit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def insert(self, table: str, data: dict[str, Any], upsert: bool = False) -> int:
        cols = list(data)
        ph = ",".join("?" * len(cols))
        verb = "INSERT OR IGNORE" if upsert else "INSERT"
        cur = self.execute(f"{verb} INTO {table} ({','.join(cols)}) VALUES ({ph})",
                           [_enc(data[c]) for c in cols])
        return int(cur.lastrowid or 0)

    def update(self, table: str, row_id: int, data: dict[str, Any]) -> None:
        if not data:
            return
        sets = ",".join(f"{k}=?" for k in data)
        self.execute(f"UPDATE {table} SET {sets} WHERE id=?",
                     [_enc(v) for v in data.values()] + [row_id])

    # -- kv ----------------------------------------------------------------
    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), _now()))

    # -- audit -------------------------------------------------------------
    def audit(self, actor: str, action: str, detail: Any = None) -> None:
        self.insert("audit_log", {"actor": actor, "action": action,
                                  "detail": _enc(detail), "created_at": _now()})

    # -- sources & knowledge ----------------------------------------------
    def add_source(self, **kw: Any) -> int:
        kw.setdefault("created_at", _now())
        kw["fulltext_verified"] = int(bool(kw.get("fulltext_verified", False)))
        existing = self.one(
            "SELECT id FROM sources WHERE source=? AND IFNULL(url,'')=? AND IFNULL(title,'')=?",
            (kw.get("source"), kw.get("url") or "", kw.get("title") or ""))
        if existing:
            return int(existing["id"])
        return self.insert("sources", kw)

    def add_knowledge(self, **kw: Any) -> int:
        kw.setdefault("created_at", _now())
        kw["explicit"] = int(bool(kw.get("explicit", False)))
        if isinstance(kw.get("quantitative_interpretation"), (list, dict)):
            kw["quantitative_interpretation"] = json.dumps(kw["quantitative_interpretation"])
        return self.insert("knowledge_items", kw)

    def knowledge(self, category: str | None = None, q: str | None = None,
                  limit: int = 500) -> list[dict[str, Any]]:
        sql = ("SELECT k.*, s.source, s.url, s.author, s.published, s.title, s.source_type, "
               "s.retrieval_method, s.fulltext_verified "
               "FROM knowledge_items k LEFT JOIN sources s ON s.id=k.source_id WHERE 1=1")
        params: list[Any] = []
        if category:
            sql += " AND k.category=?"
            params.append(category)
        if q:
            sql += (" AND (k.concept LIKE ? OR k.observation LIKE ? OR k.rule_text LIKE ? "
                    "OR k.summary LIKE ?)")
            params += [f"%{q}%"] * 4
        sql += " ORDER BY k.category, k.concept LIMIT ?"
        params.append(limit)
        rows = self.query(sql, params)
        for r in rows:
            r["quantitative_interpretation"] = _dec(r.get("quantitative_interpretation"))
            r["explicit"] = bool(r["explicit"])
        return rows

    def sources(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM sources ORDER BY source, published")

    # -- concepts ----------------------------------------------------------
    def add_concept(self, name: str, category: str, description: str = "",
                    ambiguity_note: str = "") -> int:
        row = self.one("SELECT id FROM concepts WHERE name=?", (name,))
        if row:
            return int(row["id"])
        return self.insert("concepts", {"name": name, "category": category,
                                        "description": description,
                                        "ambiguity_note": ambiguity_note,
                                        "created_at": _now()})

    def add_definition(self, concept_id: int, label: str, detector: str,
                       params: dict[str, Any], origin: str = "enumerated") -> int:
        row = self.one("SELECT id FROM candidate_definitions WHERE concept_id=? AND label=?",
                       (concept_id, label))
        if row:
            return int(row["id"])
        return self.insert("candidate_definitions",
                           {"concept_id": concept_id, "label": label, "detector": detector,
                            "params": json.dumps(params), "origin": origin,
                            "created_at": _now()})

    def concepts(self) -> list[dict[str, Any]]:
        out = self.query("SELECT * FROM concepts ORDER BY category, name")
        for c in out:
            c["definitions"] = [
                {**d, "params": _dec(d["params"])}
                for d in self.query(
                    "SELECT * FROM candidate_definitions WHERE concept_id=? ORDER BY id",
                    (c["id"],))
            ]
        return out

    # -- hypotheses & experiments -----------------------------------------
    def add_hypothesis(self, question: str, statement: str, category: str = "",
                       origin: str = "operator", spec: dict | None = None) -> int:
        now = _now()
        return self.insert("hypotheses", {
            "question": question, "statement": statement, "category": category,
            "origin": origin, "spec": json.dumps(spec or {}), "status": "OPEN",
            "created_at": now, "updated_at": now})

    def hypotheses(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM hypotheses"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY id DESC"
        rows = self.query(sql, params)
        for r in rows:
            r["spec"] = _dec(r.get("spec"))
            r["experiments"] = self.query(
                "SELECT id,name,verdict,conclusion,sample_size,created_at,data_origin "
                "FROM experiments WHERE hypothesis_id=? ORDER BY id DESC", (r["id"],))
        return rows

    def add_experiment(self, **kw: Any) -> int:
        kw.setdefault("created_at", _now())
        for k in ("spec", "results"):
            if isinstance(kw.get(k), (dict, list)):
                kw[k] = json.dumps(kw[k])
        eid = self.insert("experiments", kw)
        if kw.get("hypothesis_id"):
            self.execute("UPDATE hypotheses SET status='TESTED', verdict=?, updated_at=? "
                         "WHERE id=?", (kw.get("verdict"), _now(), kw["hypothesis_id"]))
        return eid

    def experiments(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM experiments ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["spec"] = _dec(r.get("spec"))
            r["results"] = _dec(r.get("results"))
        return rows

    def experiment(self, eid: int) -> dict[str, Any] | None:
        r = self.one("SELECT * FROM experiments WHERE id=?", (eid,))
        if r:
            r["spec"] = _dec(r.get("spec"))
            r["results"] = _dec(r.get("results"))
        return r

    # -- strategies --------------------------------------------------------
    def save_strategy(self, name: str, version: str, spec: dict, parent_id: int | None = None,
                      change_reason: str = "", notes: str = "", approved: bool = False) -> int:
        row = self.one("SELECT id FROM strategies WHERE name=? AND version=?", (name, version))
        if row:
            # Versions are immutable. Callers must bump the version instead.
            return int(row["id"])
        return self.insert("strategies", {
            "name": name, "version": version, "spec": json.dumps(spec),
            "parent_id": parent_id, "change_reason": change_reason, "notes": notes,
            "approved": int(bool(approved)), "created_at": _now()})

    def strategies(self) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM strategies ORDER BY name, version")
        for r in rows:
            r["spec"] = _dec(r["spec"])
            r["approved"] = bool(r["approved"])
        return rows

    def strategy(self, sid: int) -> dict[str, Any] | None:
        r = self.one("SELECT * FROM strategies WHERE id=?", (sid,))
        if r:
            r["spec"] = _dec(r["spec"])
            r["approved"] = bool(r["approved"])
        return r

    # -- backtests ---------------------------------------------------------
    def save_backtest(self, **kw: Any) -> int:
        kw.setdefault("created_at", _now())
        for k in ("spec", "metrics", "trades", "equity_curve", "warnings", "provenance"):
            if isinstance(kw.get(k), (dict, list)):
                kw[k] = json.dumps(kw[k])
        return self.insert("backtests", kw)

    def backtests(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT id,strategy_id,label,metrics,data_origin,data_provider,created_at,"
            "runtime_seconds,dataset_version_id,provenance FROM backtests "
            "ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["metrics"] = _dec(r["metrics"])
            r["provenance"] = _dec(r.get("provenance"))
        return rows

    def backtest(self, bid: int) -> dict[str, Any] | None:
        r = self.one("SELECT * FROM backtests WHERE id=?", (bid,))
        if r:
            for k in ("spec", "metrics", "trades", "equity_curve", "warnings",
                      "provenance"):
                r[k] = _dec(r.get(k))
        return r

    # -- journal -----------------------------------------------------------
    def add_trade(self, **kw: Any) -> int:
        now = _now()
        kw.setdefault("created_at", now)
        kw["updated_at"] = now
        if isinstance(kw.get("option_details"), dict):
            kw["option_details"] = json.dumps(kw["option_details"])
        return self.insert("trades", kw)

    def update_trade(self, tid: int, **kw: Any) -> None:
        kw["updated_at"] = _now()
        if isinstance(kw.get("option_details"), dict):
            kw["option_details"] = json.dumps(kw["option_details"])
        self.update("trades", tid, kw)

    def trades(self, limit: int = 1000, **filters: Any) -> list[dict[str, Any]]:
        sql = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []
        for k, v in filters.items():
            if v is not None:
                sql += f" AND {k}=?"
                params.append(v)
        sql += " ORDER BY IFNULL(entry_date,created_at) DESC LIMIT ?"
        params.append(limit)
        rows = self.query(sql, params)
        for r in rows:
            r["option_details"] = _dec(r.get("option_details"))
        return rows

    def delete_trade(self, tid: int) -> None:
        self.execute("DELETE FROM trades WHERE id=?", (tid,))

    # -- alerts ------------------------------------------------------------
    def add_alert(self, kind: str, condition: dict, symbol: str | None = None,
                  channels: Iterable[str] = ("inapp",)) -> int:
        return self.insert("alerts", {"kind": kind, "symbol": symbol,
                                      "condition": json.dumps(condition),
                                      "channels": json.dumps(list(channels)),
                                      "created_at": _now()})

    def alerts(self, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM alerts" + (" WHERE active=1" if active_only else "")
        rows = self.query(sql + " ORDER BY id DESC")
        for r in rows:
            r["condition"] = _dec(r["condition"])
            r["channels"] = _dec(r["channels"])
            r["active"] = bool(r["active"])
        return rows

    def fire_alert(self, alert_id: int | None, kind: str, symbol: str | None,
                   payload: dict) -> int:
        if alert_id:
            self.execute("UPDATE alerts SET last_fired=? WHERE id=?", (_now(), alert_id))
        return self.insert("alert_events", {"alert_id": alert_id, "kind": kind,
                                            "symbol": symbol, "payload": json.dumps(payload),
                                            "fired_at": _now()})

    def alert_events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM alert_events ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["payload"] = _dec(r["payload"])
        return rows

    # -- scans -------------------------------------------------------------
    def save_scan(self, as_of: str, params: dict, results: list, data_origin: str) -> int:
        return self.insert("scan_runs", {"as_of": as_of, "params": json.dumps(params),
                                         "results": json.dumps(results),
                                         "data_origin": data_origin, "created_at": _now()})

    def latest_scan(self) -> dict[str, Any] | None:
        r = self.one("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1")
        if r:
            r["params"] = _dec(r["params"])
            r["results"] = _dec(r["results"])
        return r


def _enc(v: Any) -> Any:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v)
    if isinstance(v, bool):
        return int(v)
    return v


def _dec(v: Any) -> Any:
    if v in (None, ""):
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


STORE = Store()
