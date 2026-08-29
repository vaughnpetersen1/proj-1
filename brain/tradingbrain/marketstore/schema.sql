-- ===========================================================================
-- MARKET DATA STORE  (market.db)
--
-- Separate from brain.db on purpose. This file holds bars and derived features
-- and grows into the gigabytes; brain.db holds research artefacts and stays
-- small enough to copy around. Nothing here joins across the two databases --
-- research rows reference a dataset_version id by value.
--
-- THE RAW / DERIVED SPLIT IS STRUCTURAL, NOT A CONVENTION:
--   RAW     market_data_*, raw_trades, raw_quotes, corporate_actions,
--           options_contracts, symbols
--   DERIVED technical_indicators, strategy_features, market_regimes,
--           sector_data, scanner_runs/scanner_results
-- Derived tables are recomputable and may be truncated at any time. Raw tables
-- are never written by an indicator or feature calculation; MarketStore exposes
-- no method that can do so.
-- ===========================================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------------ RAW ---

CREATE TABLE IF NOT EXISTS symbols (
    id             INTEGER PRIMARY KEY,
    symbol         TEXT NOT NULL UNIQUE,
    name           TEXT,
    exchange       TEXT,
    asset_class    TEXT DEFAULT 'us_equity',
    sector         TEXT,
    industry       TEXT,
    active         INTEGER NOT NULL DEFAULT 1,
    tradable       INTEGER,
    shortable      INTEGER,
    easy_to_borrow INTEGER,
    ipo_date       TEXT,
    delisted_date  TEXT,
    -- Where the universe row came from, so a synthetic universe can never be
    -- mistaken for a broker's live asset list.
    source         TEXT,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    note           TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbols_active ON symbols(active);
CREATE INDEX IF NOT EXISTS idx_symbols_sector ON symbols(sector);

-- One table per timeframe. Same shape everywhere so the store can address them
-- generically; separate tables so the daily table (the one every backtest hits)
-- stays small and hot while minute data grows.
--   ts        epoch seconds, UTC, bar OPEN time
--   provider  which adapter supplied the row
--   feed      which feed within that provider (iex | sip | otc | csv | synthetic)
--   adjusted  1 = split/dividend adjusted at ingestion time
CREATE TABLE IF NOT EXISTS market_data_daily (
    symbol_id   INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ts          INTEGER NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      REAL NOT NULL,
    trade_count INTEGER,
    vwap        REAL,
    provider    TEXT NOT NULL,
    feed        TEXT,
    adjusted    INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (symbol_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS market_data_1m (
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ts INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume REAL NOT NULL, trade_count INTEGER, vwap REAL,
    provider TEXT NOT NULL, feed TEXT, adjusted INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (symbol_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS market_data_5m (
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ts INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume REAL NOT NULL, trade_count INTEGER, vwap REAL,
    provider TEXT NOT NULL, feed TEXT, adjusted INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (symbol_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS market_data_15m (
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ts INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume REAL NOT NULL, trade_count INTEGER, vwap REAL,
    provider TEXT NOT NULL, feed TEXT, adjusted INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (symbol_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS market_data_1h (
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ts INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume REAL NOT NULL, trade_count INTEGER, vwap REAL,
    provider TEXT NOT NULL, feed TEXT, adjusted INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (symbol_id, ts)
) WITHOUT ROWID;

-- Tick-level retention from the realtime stream. Optional: bounded by
-- BRAIN_RAW_TICK_RETENTION_DAYS, because keeping every trade forever is the
-- fastest way to a 100GB database nobody asked for.
CREATE TABLE IF NOT EXISTS raw_trades (
    id        INTEGER PRIMARY KEY,
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ts        INTEGER NOT NULL,          -- epoch microseconds
    price     REAL NOT NULL,
    size      REAL NOT NULL,
    exchange  TEXT,
    tape      TEXT,
    conditions TEXT,
    trade_id  TEXT,
    provider  TEXT NOT NULL,
    feed      TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_trades ON raw_trades(symbol_id, ts);

CREATE TABLE IF NOT EXISTS raw_quotes (
    id        INTEGER PRIMARY KEY,
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ts        INTEGER NOT NULL,
    bid       REAL, bid_size REAL, bid_exchange TEXT,
    ask       REAL, ask_size REAL, ask_exchange TEXT,
    provider  TEXT NOT NULL,
    feed      TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_quotes ON raw_quotes(symbol_id, ts);

CREATE TABLE IF NOT EXISTS corporate_actions (
    id          INTEGER PRIMARY KEY,
    symbol_id   INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ex_date     TEXT NOT NULL,
    action_type TEXT NOT NULL,          -- split | reverse_split | cash_dividend | ...
    ratio       REAL,                   -- new/old share ratio for splits
    cash_amount REAL,
    record_date TEXT,
    payable_date TEXT,
    provider    TEXT NOT NULL,
    raw         TEXT,
    ingested_at TEXT NOT NULL,
    -- SQLite forbids expressions in UNIQUE, so the dedupe key is materialised.
    dedupe_key  TEXT NOT NULL,
    UNIQUE (dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_ca_symbol ON corporate_actions(symbol_id, ex_date);

CREATE TABLE IF NOT EXISTS options_contracts (
    id             INTEGER PRIMARY KEY,
    underlying_id  INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    contract       TEXT NOT NULL,
    expiration     TEXT NOT NULL,
    strike         REAL NOT NULL,
    option_type    TEXT NOT NULL,
    style          TEXT,
    open_interest  REAL,
    bid REAL, ask REAL, last REAL, volume REAL,
    implied_volatility REAL,
    delta REAL, gamma REAL, theta REAL, vega REAL, rho REAL,
    as_of          TEXT NOT NULL,
    provider       TEXT NOT NULL,
    feed           TEXT,
    UNIQUE (contract, as_of)
);
CREATE INDEX IF NOT EXISTS idx_opt_underlying ON options_contracts(underlying_id, expiration);

-- -------------------------------------------------------------- SYNC ------

CREATE TABLE IF NOT EXISTS data_sync_status (
    symbol_id            INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    timeframe            TEXT NOT NULL,
    provider             TEXT,
    feed                 TEXT,
    first_ts             INTEGER,
    last_ts              INTEGER,
    rows                 INTEGER NOT NULL DEFAULT 0,
    last_successful_sync TEXT,
    last_attempt         TEXT,
    status               TEXT NOT NULL DEFAULT 'never',  -- never|ok|partial|error|stale
    error                TEXT,
    requested_start      TEXT,
    PRIMARY KEY (symbol_id, timeframe)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,
    level     TEXT NOT NULL,            -- info | warning | error
    kind      TEXT NOT NULL,            -- backfill | realtime | validation | features | ...
    symbol    TEXT,
    timeframe TEXT,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_log_ts ON ingest_log(ts DESC);

CREATE TABLE IF NOT EXISTS api_usage (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,
    provider  TEXT NOT NULL,
    endpoint  TEXT NOT NULL,
    symbols   INTEGER DEFAULT 1,
    rows      INTEGER DEFAULT 0,
    ok        INTEGER NOT NULL DEFAULT 1,
    ms        REAL,
    error     TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_usage_ts ON api_usage(ts DESC);

CREATE TABLE IF NOT EXISTS data_quality_flags (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    code      TEXT NOT NULL,
    severity  TEXT NOT NULL,
    detail    TEXT,
    sample    TEXT,
    resolved  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dq_symbol ON data_quality_flags(symbol, resolved);

-- ---------------------------------------------------------- DATASET LOCK --
-- A backtest points at one of these rows, so a result stays reproducible even
-- after the database has been extended, re-adjusted or re-provided.
CREATE TABLE IF NOT EXISTS dataset_versions (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL,
    label         TEXT,
    provider      TEXT NOT NULL,
    feed          TEXT,
    origin        TEXT NOT NULL,        -- REAL | SYNTHETIC | MIXED
    timeframe     TEXT NOT NULL,
    start_ts      INTEGER,
    end_ts        INTEGER,
    symbol_count  INTEGER NOT NULL,
    row_count     INTEGER NOT NULL,
    universe_hash TEXT NOT NULL,
    universe      TEXT NOT NULL,        -- JSON list of symbols
    adjustment    TEXT,                 -- split_and_dividend | split_only | none | unknown
    checksum      TEXT NOT NULL,        -- content hash of the bars actually used
    note          TEXT,
    UNIQUE (checksum)
);

-- -------------------------------------------------------------- DERIVED ---
-- Everything below is recomputable from the raw tables above. `feature_version`
-- lets a recalculation invalidate stale rows without dropping the table.

CREATE TABLE IF NOT EXISTS technical_indicators (
    symbol_id  INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ts         INTEGER NOT NULL,
    timeframe  TEXT NOT NULL DEFAULT '1d',
    sma10 REAL, sma20 REAL, sma50 REAL, sma200 REAL,
    ema21 REAL,
    rsi14 REAL,
    atr14 REAL,
    adr20 REAL,
    macd REAL, macd_signal REAL, macd_hist REAL,
    vwap REAL,
    relative_volume REAL,
    dollar_volume_20d REAL,
    r2_20 REAL,
    computed_at TEXT NOT NULL,
    feature_version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (symbol_id, timeframe, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS strategy_features (
    symbol_id  INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    ts         INTEGER NOT NULL,
    timeframe  TEXT NOT NULL DEFAULT '1d',
    close REAL, volume REAL,
    prior_5d_return REAL, prior_10d_return REAL, prior_20d_return REAL,
    prior_60d_return REAL, prior_120d_return REAL,
    prior_move_pct REAL, prior_move_bars INTEGER,
    distance_from_10sma REAL, distance_from_20sma REAL,
    distance_from_50sma REAL, distance_from_200sma REAL,
    above_10sma INTEGER, above_20sma INTEGER, above_50sma INTEGER, above_200sma INTEGER,
    sma_stack_10_20_50 INTEGER,
    consolidation_days INTEGER,
    consolidation_high REAL, consolidation_low REAL,
    consolidation_depth_pct REAL,
    range_contraction REAL, volume_contraction REAL,
    higher_low_ratio REAL, base_quality REAL, base_qualifies INTEGER,
    breakout_distance_pct REAL, breakout_today INTEGER,
    breakout_volume_ratio REAL, close_position REAL,
    pct_from_52w_high REAL,
    relative_volume REAL, adr20 REAL, atr14 REAL,
    dollar_volume_20d REAL,
    market_relative_strength REAL,     -- return vs the index over rs_lookback
    relative_strength_pct REAL,        -- cross-sectional percentile, 0-100
    sector_relative_strength REAL,
    computed_at TEXT NOT NULL,
    feature_version INTEGER NOT NULL DEFAULT 1,
    config_hash TEXT,                  -- hash of the detector params used
    PRIMARY KEY (symbol_id, timeframe, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_sf_ts ON strategy_features(ts);

CREATE TABLE IF NOT EXISTS market_regimes (
    as_of      TEXT NOT NULL,
    index_set  TEXT NOT NULL DEFAULT 'SPY,QQQ',
    label      TEXT NOT NULL,
    volatility_label TEXT,
    strength   REAL,
    filter_pass INTEGER,
    breadth_above_50sma REAL,
    breadth_above_200sma REAL,
    components TEXT,
    provider   TEXT, feed TEXT, origin TEXT,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (as_of, index_set)
);

CREATE TABLE IF NOT EXISTS sector_data (
    as_of      TEXT NOT NULL,
    kind       TEXT NOT NULL,          -- sector | theme
    name       TEXT NOT NULL,
    rank       INTEGER NOT NULL,
    score      REAL,
    members    INTEGER,
    components TEXT,
    leaders    TEXT,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (as_of, kind, name)
);

CREATE TABLE IF NOT EXISTS scanner_runs (
    id          INTEGER PRIMARY KEY,
    as_of       TEXT NOT NULL,
    strategy    TEXT,
    config      TEXT NOT NULL,          -- the exact filter configuration used
    config_hash TEXT NOT NULL,
    engine      TEXT NOT NULL DEFAULT 'sql',
    symbols_scanned INTEGER,
    matched     INTEGER,
    data_origin TEXT, provider TEXT, feed TEXT,
    runtime_ms  REAL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scanner_results (
    run_id     INTEGER NOT NULL REFERENCES scanner_runs(id) ON DELETE CASCADE,
    rank       INTEGER NOT NULL,
    symbol     TEXT NOT NULL,
    score      REAL,
    state      TEXT,
    sector     TEXT,
    payload    TEXT,
    PRIMARY KEY (run_id, symbol)
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
