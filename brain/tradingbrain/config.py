"""Configuration. Every secret comes from the environment; nothing is committed."""

from __future__ import annotations

import dataclasses
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data_cache" / "brain.db"
DEFAULT_CACHE = ROOT / "data_cache"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


@dataclasses.dataclass
class Settings:
    # --- storage ---------------------------------------------------------
    db_path: pathlib.Path = DEFAULT_DB
    #: Bars and derived features. Separate from db_path because it grows into
    #: the gigabytes while the research database stays small.
    market_db_path: pathlib.Path = DEFAULT_CACHE / "market.db"
    cache_dir: pathlib.Path = DEFAULT_CACHE

    # --- data providers --------------------------------------------------
    # Ordered preference. First provider that is configured *and* reachable wins.
    #: READ path. "db" is first on purpose: engines read the local store, and a
    #: miss triggers a persisted backfill rather than a throwaway fetch.
    provider_order: tuple[str, ...] = ("db", "alpaca", "csv", "stooq", "yahoo",
                                       "tiingo", "polygon", "synthetic")
    #: When the store misses, fetch the missing range once and persist it.
    #: Set false to make the system strictly offline: a miss becomes an error
    #: naming the symbol and range to backfill.
    allow_ondemand_backfill: bool = True
    tiingo_api_key: str | None = None
    polygon_api_key: str | None = None
    alpaca_api_key: str | None = None
    alpaca_api_secret: str | None = None
    alpaca_data_feed: str = "iex"
    alpaca_paper: bool = True
    finnhub_api_key: str | None = None
    news_api_key: str | None = None

    # --- llm -------------------------------------------------------------
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    llm_enabled: bool = False

    # --- execution assumptions (backtests) -------------------------------
    commission_per_share: float = 0.0
    commission_min: float = 0.0
    slippage_bps: float = 5.0
    borrow_cost_bps_per_day: float = 0.0

    # --- trading safety --------------------------------------------------
    live_trading_enabled: bool = False       # NEVER default true
    paper_trading_enabled: bool = True
    kill_switch: bool = False
    max_daily_loss_pct: float = 3.0
    max_position_pct: float = 20.0
    max_portfolio_exposure_pct: float = 100.0
    max_order_notional: float = 5_000.0

    # --- data ingestion --------------------------------------------------
    #: Providers tried, in order, by the ingestion service when backfilling.
    ingest_provider_order: tuple[str, ...] = ("alpaca", "tiingo", "polygon",
                                              "stooq", "yahoo", "csv", "synthetic")
    history_years: float = 5.0
    ingest_batch_symbols: int = 100
    ingest_max_symbols: int = 0            # 0 = no cap
    realtime_enabled: bool = False
    realtime_symbols: tuple[str, ...] = ()
    realtime_channels: tuple[str, ...] = ("bars",)
    store_raw_ticks: bool = False
    raw_tick_retention_days: int = 5
    reconnect_max_backoff: float = 60.0
    feature_version: int = 1

    # --- server ----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8787

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.db_path = pathlib.Path(os.environ.get("BRAIN_DB", str(DEFAULT_DB)))
        s.cache_dir = pathlib.Path(os.environ.get("BRAIN_CACHE_DIR", str(DEFAULT_CACHE)))
        s.market_db_path = pathlib.Path(
            os.environ.get("BRAIN_MARKET_DB", str(s.cache_dir / "market.db")))
        order = os.environ.get("BRAIN_PROVIDER_ORDER")
        if order:
            s.provider_order = tuple(p.strip() for p in order.split(",") if p.strip())
        s.allow_ondemand_backfill = _env_bool("BRAIN_ALLOW_ONDEMAND_BACKFILL", True)
        s.tiingo_api_key = os.environ.get("TIINGO_API_KEY") or None
        s.polygon_api_key = os.environ.get("POLYGON_API_KEY") or None
        s.alpaca_api_key = (os.environ.get("ALPACA_API_KEY")
                            or os.environ.get("ALPACA_KEY_ID") or None)
        s.alpaca_api_secret = (os.environ.get("ALPACA_API_SECRET")
                               or os.environ.get("ALPACA_SECRET_KEY") or None)
        s.alpaca_data_feed = (os.environ.get("ALPACA_DATA_FEED") or "iex").lower()
        s.alpaca_paper = _env_bool("ALPACA_PAPER", True)
        s.finnhub_api_key = os.environ.get("FINNHUB_API_KEY") or None
        s.news_api_key = os.environ.get("NEWS_API_KEY") or None
        s.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY") or None
        s.anthropic_model = os.environ.get("BRAIN_LLM_MODEL", s.anthropic_model)
        s.llm_enabled = bool(s.anthropic_api_key) and _env_bool("BRAIN_LLM_ENABLED", True)
        s.commission_per_share = _env_float("BRAIN_COMMISSION_PER_SHARE", s.commission_per_share)
        s.commission_min = _env_float("BRAIN_COMMISSION_MIN", s.commission_min)
        s.slippage_bps = _env_float("BRAIN_SLIPPAGE_BPS", s.slippage_bps)
        s.live_trading_enabled = _env_bool("BRAIN_LIVE_TRADING_ENABLED", False)
        s.kill_switch = _env_bool("BRAIN_KILL_SWITCH", False)
        s.max_daily_loss_pct = _env_float("BRAIN_MAX_DAILY_LOSS_PCT", s.max_daily_loss_pct)
        s.max_position_pct = _env_float("BRAIN_MAX_POSITION_PCT", s.max_position_pct)
        s.max_order_notional = _env_float("BRAIN_MAX_ORDER_NOTIONAL", s.max_order_notional)
        order = os.environ.get("BRAIN_INGEST_PROVIDER_ORDER")
        if order:
            s.ingest_provider_order = tuple(p.strip() for p in order.split(",") if p.strip())
        s.history_years = _env_float("BRAIN_HISTORY_YEARS", s.history_years)
        s.ingest_batch_symbols = int(_env_float("BRAIN_INGEST_BATCH_SYMBOLS",
                                                s.ingest_batch_symbols))
        s.ingest_max_symbols = int(_env_float("BRAIN_INGEST_MAX_SYMBOLS",
                                              s.ingest_max_symbols))
        s.realtime_enabled = _env_bool("BRAIN_REALTIME_ENABLED", False)
        rts = os.environ.get("BRAIN_REALTIME_SYMBOLS")
        if rts:
            s.realtime_symbols = tuple(x.strip().upper() for x in rts.split(",") if x.strip())
        rtc = os.environ.get("BRAIN_REALTIME_CHANNELS")
        if rtc:
            s.realtime_channels = tuple(x.strip() for x in rtc.split(",") if x.strip())
        s.store_raw_ticks = _env_bool("BRAIN_STORE_RAW_TICKS", False)
        s.raw_tick_retention_days = int(_env_float("BRAIN_RAW_TICK_RETENTION_DAYS",
                                                   s.raw_tick_retention_days))
        s.reconnect_max_backoff = _env_float("BRAIN_RECONNECT_MAX_BACKOFF",
                                             s.reconnect_max_backoff)
        s.host = os.environ.get("BRAIN_HOST", s.host)
        try:
            s.port = int(os.environ.get("BRAIN_PORT", s.port))
        except ValueError:
            pass
        s.cache_dir.mkdir(parents=True, exist_ok=True)
        s.db_path.parent.mkdir(parents=True, exist_ok=True)
        s.market_db_path.parent.mkdir(parents=True, exist_ok=True)
        return s

    def redacted(self) -> dict[str, object]:
        d = dataclasses.asdict(self)
        for k in list(d):
            if k.endswith(("_key", "_key_id", "_secret_key", "_api_key", "_api_secret",
                           "_secret")):
                d[k] = "<set>" if d[k] else None
            elif isinstance(d[k], pathlib.Path):
                d[k] = str(d[k])
            elif isinstance(d[k], tuple):
                d[k] = list(d[k])
        return d


SETTINGS = Settings.from_env()
