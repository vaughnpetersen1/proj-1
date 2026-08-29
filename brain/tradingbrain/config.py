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
    cache_dir: pathlib.Path = DEFAULT_CACHE

    # --- data providers --------------------------------------------------
    # Ordered preference. First provider that is configured *and* reachable wins.
    provider_order: tuple[str, ...] = ("csv", "stooq", "yahoo", "tiingo", "polygon", "synthetic")
    tiingo_api_key: str | None = None
    polygon_api_key: str | None = None
    alpaca_key_id: str | None = None
    alpaca_secret_key: str | None = None
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

    # --- server ----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8787

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.db_path = pathlib.Path(os.environ.get("BRAIN_DB", str(DEFAULT_DB)))
        s.cache_dir = pathlib.Path(os.environ.get("BRAIN_CACHE_DIR", str(DEFAULT_CACHE)))
        order = os.environ.get("BRAIN_PROVIDER_ORDER")
        if order:
            s.provider_order = tuple(p.strip() for p in order.split(",") if p.strip())
        s.tiingo_api_key = os.environ.get("TIINGO_API_KEY") or None
        s.polygon_api_key = os.environ.get("POLYGON_API_KEY") or None
        s.alpaca_key_id = os.environ.get("ALPACA_KEY_ID") or None
        s.alpaca_secret_key = os.environ.get("ALPACA_SECRET_KEY") or None
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
        s.host = os.environ.get("BRAIN_HOST", s.host)
        try:
            s.port = int(os.environ.get("BRAIN_PORT", s.port))
        except ValueError:
            pass
        s.cache_dir.mkdir(parents=True, exist_ok=True)
        s.db_path.parent.mkdir(parents=True, exist_ok=True)
        return s

    def redacted(self) -> dict[str, object]:
        d = dataclasses.asdict(self)
        for k in list(d):
            if k.endswith(("_key", "_key_id", "_secret_key")):
                d[k] = "<set>" if d[k] else None
            elif isinstance(d[k], pathlib.Path):
                d[k] = str(d[k])
            elif isinstance(d[k], tuple):
                d[k] = list(d[k])
        return d


SETTINGS = Settings.from_env()
