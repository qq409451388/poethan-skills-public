-- migration: foreign_keys_off
-- Event 是可收敛的唤醒信号；Turn 指标只保存计数，不保存上下文正文。

CREATE TABLE code_inspector_event_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    issue_key TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('inspector', 'developer')),
    operator_id TEXT NOT NULL,
    agent_platform TEXT NOT NULL,
    runtime_backend TEXT NOT NULL,
    event_type TEXT NOT NULL,
    activity_id INTEGER,
    stage_no INTEGER,
    projection_revision INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','PROCESSING','DONE','FAILED','SUPERSEDED')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    lease_until TEXT,
    worker_id TEXT,
    next_attempt_at TEXT,
    failure_kind TEXT CHECK (failure_kind IS NULL OR failure_kind IN ('RETRYABLE','NON_RETRYABLE','AMBIGUOUS')),
    last_error TEXT,
    superseded_by_event_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO code_inspector_event_new (
    id,event_id,idempotency_key,issue_key,role,operator_id,agent_platform,runtime_backend,
    event_type,activity_id,stage_no,status,attempt_count,claimed_at,lease_until,worker_id,
    next_attempt_at,failure_kind,last_error,created_at,updated_at
)
SELECT id,event_id,idempotency_key,issue_key,role,operator_id,agent_platform,runtime_backend,
       event_type,activity_id,stage_no,status,attempt_count,claimed_at,lease_until,worker_id,
       next_attempt_at,failure_kind,last_error,created_at,updated_at
FROM code_inspector_event;

DROP TABLE code_inspector_event;
ALTER TABLE code_inspector_event_new RENAME TO code_inspector_event;
CREATE INDEX idx_code_inspector_event_dispatch ON code_inspector_event(status, next_attempt_at, created_at, id);
CREATE INDEX idx_code_inspector_event_order ON code_inspector_event(issue_key, operator_id, status, id);
CREATE INDEX idx_code_inspector_event_lease ON code_inspector_event(status, lease_until);

CREATE TABLE code_inspector_turn_metric (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT,
    issue_key TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('inspector', 'developer')),
    operator_id TEXT NOT NULL,
    projection_revision INTEGER NOT NULL,
    turn_type TEXT NOT NULL CHECK (turn_type IN ('INIT','ACTION','COMPACT')),
    turn_id TEXT,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,
    review_db_calls_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_code_inspector_turn_metric_issue
    ON code_inspector_turn_metric(issue_key, role, created_at, id);
CREATE INDEX idx_code_inspector_turn_metric_event
    ON code_inspector_turn_metric(event_id, turn_type);
