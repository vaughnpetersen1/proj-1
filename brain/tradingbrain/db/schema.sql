-- AI Trading Brain persistent store.
-- Design rule: nothing here stores a *conclusion* without the evidence and the
-- provenance that produced it. Every research artefact is reproducible from
-- what is written in these tables.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- sources --
CREATE TABLE IF NOT EXISTS sources (
    id                INTEGER PRIMARY KEY,
    source            TEXT NOT NULL,          -- e.g. "Qullamaggie (first-party site)"
    url               TEXT,
    author            TEXT,
    published         TEXT,                   -- ISO date if known
    title             TEXT,
    source_type       TEXT,                   -- video | article | interview | thread | brief
    topic             TEXT,
    retrieval_method  TEXT,                   -- web_search_summary | fulltext_fetch | operator_supplied
    fulltext_verified INTEGER NOT NULL DEFAULT 0,
    note              TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE (source, url, title)
);

-- ------------------------------------------------------- knowledge items --
-- One extracted trading concept from one source.
CREATE TABLE IF NOT EXISTS knowledge_items (
    id                        INTEGER PRIMARY KEY,
    source_id                 INTEGER REFERENCES sources(id) ON DELETE CASCADE,
    category                  TEXT NOT NULL,   -- MARKET_REGIME | SECTOR_THEME | STOCK_SELECTION | SETUP | ENTRY | RISK | TRADE_MANAGEMENT
    concept                   TEXT NOT NULL,   -- "tight consolidation"
    observation               TEXT NOT NULL,   -- what the source actually conveys
    quote                     TEXT,            -- short verbatim excerpt, only when legally appropriate
    summary                   TEXT,
    rule_text                 TEXT,            -- the rule implied by the concept
    explicit                  INTEGER NOT NULL DEFAULT 0,  -- 1 = stated outright, 0 = inferred
    quantitative_interpretation TEXT,          -- JSON: list of candidate measurable definitions
    backtest_hypothesis       TEXT,
    related_component         TEXT,
    evidence_class            TEXT NOT NULL DEFAULT 'SOURCE_FACT',
    confidence_note           TEXT,
    created_at                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ki_category ON knowledge_items(category);
CREATE INDEX IF NOT EXISTS idx_ki_concept  ON knowledge_items(concept);

-- --------------------------------------------------- qualitative concepts --
-- The formalisation layer: a fuzzy phrase and every competing way to measure it.
CREATE TABLE IF NOT EXISTS concepts (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,        -- "strong prior move"
    category      TEXT NOT NULL,
    description   TEXT,
    ambiguity_note TEXT,                       -- preserved, never resolved by fiat
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_definitions (
    id            INTEGER PRIMARY KEY,
    concept_id    INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    label         TEXT NOT NULL,               -- "+30% in 20 trading days"
    params        TEXT NOT NULL,               -- JSON parameters fed to the detector
    detector      TEXT NOT NULL,               -- name of the function that evaluates it
    origin        TEXT,                        -- source id(s) or 'enumerated'
    created_at    TEXT NOT NULL,
    UNIQUE (concept_id, label)
);

-- ------------------------------------------------------------ hypotheses --
CREATE TABLE IF NOT EXISTS hypotheses (
    id            INTEGER PRIMARY KEY,
    question      TEXT NOT NULL,
    statement     TEXT NOT NULL,
    category      TEXT,
    origin        TEXT,                        -- 'operator' | 'source:<id>' | 'auto'
    status        TEXT NOT NULL DEFAULT 'OPEN',-- OPEN | TESTED | RETIRED
    verdict       TEXT,                        -- SUPPORTED | MIXED | UNSUPPORTED | INSUFFICIENT_DATA
    spec          TEXT,                        -- JSON experiment specification
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    id              INTEGER PRIMARY KEY,
    hypothesis_id   INTEGER REFERENCES hypotheses(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    spec            TEXT NOT NULL,             -- JSON: universe, period, groups, params, costs
    results         TEXT,                      -- JSON: full statistics for every group
    conclusion      TEXT,
    verdict         TEXT,
    sample_size     INTEGER,
    data_origin     TEXT,                      -- REAL | SYNTHETIC
    data_provider   TEXT,
    code_version    TEXT,
    runtime_seconds REAL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exp_hyp ON experiments(hypothesis_id);

-- ------------------------------------------------------------ strategies --
CREATE TABLE IF NOT EXISTS strategies (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    version       TEXT NOT NULL,
    spec          TEXT NOT NULL,               -- JSON StrategySpec
    parent_id     INTEGER REFERENCES strategies(id),
    change_reason TEXT,
    approved      INTEGER NOT NULL DEFAULT 0,  -- automated research may NOT set this
    notes         TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS backtests (
    id            INTEGER PRIMARY KEY,
    strategy_id   INTEGER REFERENCES strategies(id) ON DELETE SET NULL,
    label         TEXT,
    spec          TEXT NOT NULL,
    metrics       TEXT NOT NULL,
    trades        TEXT,
    equity_curve  TEXT,
    data_origin   TEXT,
    data_provider TEXT,
    warnings      TEXT,
    runtime_seconds REAL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bt_strategy ON backtests(strategy_id);

-- --------------------------------------------------------- trade journal --
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY,
    ticker          TEXT NOT NULL,
    direction       TEXT NOT NULL DEFAULT 'long',
    strategy        TEXT,
    setup           TEXT,
    entry_date      TEXT,
    entry_price     REAL,
    stop_price      REAL,
    target_price    REAL,
    position_size   REAL,
    exit_date       TEXT,
    exit_price      REAL,
    pnl             REAL,
    r_multiple      REAL,
    fees            REAL DEFAULT 0,
    instrument      TEXT DEFAULT 'stock',      -- stock | option
    option_details  TEXT,                      -- JSON
    screenshot_path TEXT,
    thesis          TEXT,
    market_regime   TEXT,
    sector          TEXT,
    ai_analysis     TEXT,
    reason_entry    TEXT,
    reason_exit     TEXT,
    mistakes        TEXT,
    notes           TEXT,
    tags            TEXT,
    account_equity_at_entry REAL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_entry  ON trades(entry_date);

-- ---------------------------------------------------------------- alerts --
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    symbol       TEXT,
    condition    TEXT NOT NULL,                -- JSON
    channels     TEXT NOT NULL DEFAULT '["inapp"]',
    active       INTEGER NOT NULL DEFAULT 1,
    last_fired   TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_events (
    id         INTEGER PRIMARY KEY,
    alert_id   INTEGER REFERENCES alerts(id) ON DELETE CASCADE,
    symbol     TEXT,
    kind       TEXT,
    payload    TEXT,
    fired_at   TEXT NOT NULL,
    delivered  INTEGER NOT NULL DEFAULT 0
);

-- ----------------------------------------------------------------- scans --
CREATE TABLE IF NOT EXISTS scan_runs (
    id           INTEGER PRIMARY KEY,
    as_of        TEXT NOT NULL,
    params       TEXT NOT NULL,
    results      TEXT NOT NULL,
    data_origin  TEXT,
    created_at   TEXT NOT NULL
);

-- ------------------------------------------------------------ misc / kv --
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY,
    actor      TEXT NOT NULL,        -- operator | research_automation | ai
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
