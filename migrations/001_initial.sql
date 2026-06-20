-- migrations/001_initial.sql
-- Signal Copier v1 schema. Idempotent: safe to run on every boot.
-- See docs/PRD.md §9 for the full design rationale.

CREATE TABLE IF NOT EXISTS signals (
    signal_id          TEXT PRIMARY KEY,
    pair               TEXT NOT NULL,
    broker_pair        TEXT,
    broker_category    TEXT,
    direction          TEXT NOT NULL CHECK (direction IN ('up', 'down')),
    trigger_hhmm       TEXT NOT NULL,
    trigger_ts_unix    DOUBLE PRECISION NOT NULL,
    expiration_seconds INTEGER NOT NULL,
    received_at_unix   DOUBLE PRECISION NOT NULL,
    source_message_id  BIGINT,
    source_chat_id     BIGINT,
    raw_text           TEXT,
    status             TEXT NOT NULL
        CHECK (status IN (
            'pending', 'placed_initial', 'placed_gale1', 'placed_gale2',
            'done_win', 'done_loss', 'done_tie', 'done_timeout', 'error'
        )),
    error_reason       TEXT,
    created_at_unix    DOUBLE PRECISION NOT NULL,
    updated_at_unix    DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS stages (
    trade_id           TEXT PRIMARY KEY,
    signal_id          TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    stage              TEXT NOT NULL CHECK (stage IN ('initial', 'gale1', 'gale2')),
    pair               TEXT NOT NULL,
    direction          TEXT NOT NULL,
    amount             DOUBLE PRECISION NOT NULL,
    placed_at_unix     DOUBLE PRECISION NOT NULL,
    expires_at_unix    DOUBLE PRECISION NOT NULL,
    closed_at_unix     DOUBLE PRECISION,
    pnl                DOUBLE PRECISION,
    result             TEXT CHECK (result IN ('open', 'win', 'loss', 'tie', 'timeout', 'error')),
    broker_trade_id    TEXT
);

CREATE TABLE IF NOT EXISTS daily_summary (
    date              DATE PRIMARY KEY,
    signals_count     INTEGER NOT NULL DEFAULT 0,
    trades_count      INTEGER NOT NULL DEFAULT 0,
    wins              INTEGER NOT NULL DEFAULT 0,
    losses            INTEGER NOT NULL DEFAULT 0,
    realized_pnl      DOUBLE PRECISION NOT NULL DEFAULT 0,
    limit_hit         TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_status      ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_trigger_ts  ON signals(trigger_ts_unix);
CREATE INDEX IF NOT EXISTS idx_stages_signal_id    ON stages(signal_id);
CREATE INDEX IF NOT EXISTS idx_stages_placed_at    ON stages(placed_at_unix);
CREATE INDEX IF NOT EXISTS idx_stages_result       ON stages(result);