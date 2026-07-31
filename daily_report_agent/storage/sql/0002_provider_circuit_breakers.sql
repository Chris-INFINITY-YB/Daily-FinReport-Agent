CREATE TABLE provider_circuit_breakers (
    provider_id TEXT NOT NULL CHECK (length(trim(provider_id)) > 0),
    operation TEXT NOT NULL CHECK (length(trim(operation)) > 0),
    state TEXT NOT NULL CHECK (state IN ('closed', 'open', 'half_open')),
    consecutive_failures INTEGER NOT NULL CHECK (consecutive_failures >= 0),
    opened_at TEXT NULL,
    open_until TEXT NULL,
    last_transition_at TEXT NOT NULL,
    last_failure_at TEXT NULL,
    last_success_at TEXT NULL,
    last_error_code TEXT NULL,
    half_open_probe_active INTEGER NOT NULL CHECK (
        half_open_probe_active IN (0, 1)
    ),
    version INTEGER NOT NULL CHECK (version >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider_id, operation)
);

CREATE INDEX idx_provider_circuit_breakers_state_open_until
    ON provider_circuit_breakers(state, open_until);
