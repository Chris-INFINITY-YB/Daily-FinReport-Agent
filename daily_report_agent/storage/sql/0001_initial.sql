CREATE TABLE securities (
    id INTEGER PRIMARY KEY,
    market TEXT NOT NULL CHECK (market IN ('cn', 'us')),
    symbol TEXT NOT NULL,
    exchange TEXT NULL,
    name TEXT NOT NULL,
    currency TEXT NULL,
    industry TEXT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (market, symbol)
);

CREATE INDEX idx_securities_market_symbol ON securities(market, symbol);
CREATE INDEX idx_securities_exchange_symbol ON securities(exchange, symbol);
CREATE INDEX idx_securities_is_active ON securities(is_active);

CREATE TABLE security_aliases (
    security_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    PRIMARY KEY (security_id, alias),
    FOREIGN KEY (security_id) REFERENCES securities(id) ON DELETE RESTRICT
);

CREATE TABLE news_items (
    id TEXT PRIMARY KEY,
    external_id TEXT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NULL,
    url TEXT NULL,
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    language TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_reliability REAL NULL CHECK (
        source_reliability IS NULL
        OR (source_reliability >= 0 AND source_reliability <= 1)
    ),
    raw_response_id INTEGER NULL,
    FOREIGN KEY (raw_response_id) REFERENCES raw_responses(id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX uq_news_source_external_id
    ON news_items(source, external_id)
    WHERE external_id IS NOT NULL;
CREATE UNIQUE INDEX uq_news_source_content_hash
    ON news_items(source, content_hash)
    WHERE external_id IS NULL;
CREATE INDEX idx_news_published_at ON news_items(published_at);

CREATE TABLE news_security_links (
    news_id TEXT NOT NULL,
    security_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'mentioned',
    confidence REAL NULL CHECK (
        confidence IS NULL OR (confidence >= 0 AND confidence <= 1)
    ),
    created_at TEXT NOT NULL,
    PRIMARY KEY (news_id, security_id),
    FOREIGN KEY (news_id) REFERENCES news_items(id) ON DELETE RESTRICT,
    FOREIGN KEY (security_id) REFERENCES securities(id) ON DELETE RESTRICT
);

CREATE INDEX idx_news_links_security_id ON news_security_links(security_id);

CREATE TABLE market_snapshots (
    id INTEGER PRIMARY KEY,
    security_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    price REAL NULL,
    previous_close REAL NULL,
    pct_change REAL NULL,
    volume REAL NULL,
    amount REAL NULL,
    turnover REAL NULL,
    pe_ttm REAL NULL,
    pb REAL NULL,
    market_cap REAL NULL,
    currency TEXT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_response_id INTEGER NULL,
    UNIQUE (security_id, source, observed_at),
    FOREIGN KEY (security_id) REFERENCES securities(id) ON DELETE RESTRICT,
    FOREIGN KEY (raw_response_id) REFERENCES raw_responses(id) ON DELETE RESTRICT
);

CREATE INDEX idx_market_snapshots_security_time
    ON market_snapshots(security_id, observed_at);

CREATE TABLE pipeline_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
    dry_run INTEGER NOT NULL CHECK (dry_run IN (0, 1)),
    config_hash TEXT NULL,
    app_version TEXT NULL,
    error_summary TEXT NULL,
    created_news INTEGER NOT NULL DEFAULT 0 CHECK (created_news >= 0),
    created_snapshots INTEGER NOT NULL DEFAULT 0 CHECK (created_snapshots >= 0)
);

CREATE INDEX idx_pipeline_runs_started_at ON pipeline_runs(started_at);
CREATE INDEX idx_pipeline_runs_status ON pipeline_runs(status);

CREATE TABLE provider_calls (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    security_id INTEGER NULL,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NULL,
    duration_ms INTEGER NULL CHECK (duration_ms IS NULL OR duration_ms >= 0),
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'empty', 'failed', 'skipped')),
    item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    error_severity TEXT NULL CHECK (
        error_severity IS NULL OR error_severity IN ('error', 'warning', 'info')
    ),
    error_category TEXT NULL CHECK (
        error_category IS NULL OR error_category IN (
            'network', 'parse', 'auth', 'missing_data', 'rate_limit',
            'validation', 'provider_unavailable', 'unknown'
        )
    ),
    error_code TEXT NULL,
    error_message TEXT NULL,
    request_fingerprint TEXT NULL,
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id) ON DELETE RESTRICT,
    FOREIGN KEY (security_id) REFERENCES securities(id) ON DELETE RESTRICT
);

CREATE INDEX idx_provider_calls_run_id ON provider_calls(run_id);
CREATE INDEX idx_provider_calls_security_id ON provider_calls(security_id);

CREATE TABLE raw_responses (
    id INTEGER PRIMARY KEY,
    provider_call_id INTEGER NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL,
    http_status INTEGER NULL,
    content_type TEXT NULL,
    body BLOB NULL,
    body_sha256 TEXT NOT NULL,
    compression TEXT NULL,
    is_redacted INTEGER NOT NULL DEFAULT 1 CHECK (is_redacted IN (0, 1)),
    UNIQUE (provider_call_id, sequence),
    FOREIGN KEY (provider_call_id) REFERENCES provider_calls(id) ON DELETE RESTRICT
);

CREATE INDEX idx_raw_responses_provider_call_id ON raw_responses(provider_call_id);
