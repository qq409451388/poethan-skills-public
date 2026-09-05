-- migration: foreign_keys_off
-- Runtime identity boundary, transactional outbox metadata and crash-recovery leases.

CREATE TABLE code_inspector_thread_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    issue_key TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('inspector', 'developer')),
    operator_id TEXT NOT NULL,
    agent_platform TEXT NOT NULL,
    runtime_backend TEXT NOT NULL,
    thread_id TEXT NOT NULL UNIQUE,
    thread_status TEXT NOT NULL CHECK (thread_status IN ('INITIALIZING','ACTIVE','WAITING','PAUSED','COMPLETED','FAILED','ARCHIVED')),
    issue_status TEXT,
    next_action TEXT,
    last_event TEXT,
    cwd TEXT NOT NULL,
    context_tokens INTEGER,
    context_window INTEGER,
    last_compact_at TEXT,
    last_compact_stage_no INTEGER,
    error_code TEXT,
    error_message TEXT,
    worker_id TEXT,
    lease_until TEXT,
    heartbeat_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_active_at TEXT,
    FOREIGN KEY(issue_id) REFERENCES review_issue(id) ON DELETE CASCADE,
    UNIQUE(issue_key, operator_id)
);

INSERT INTO code_inspector_thread_new (
    id, issue_id, issue_key, role, operator_id, agent_platform, runtime_backend,
    thread_id, thread_status, issue_status, next_action, last_event, cwd,
    context_tokens, context_window, last_compact_at, last_compact_stage_no,
    error_code, error_message, created_at, updated_at, last_active_at
)
SELECT id, issue_id, issue_key, role,
       CASE role WHEN 'developer' THEN 'codex-dev' ELSE 'codex-insp' END,
       'codex', 'codex-app-server', thread_id, thread_status, issue_status,
       next_action, last_event, cwd, context_tokens, context_window,
       last_compact_at, last_compact_stage_no, error_code, error_message,
       created_at, updated_at, last_active_at
FROM code_inspector_thread;

DROP TABLE code_inspector_thread;
ALTER TABLE code_inspector_thread_new RENAME TO code_inspector_thread;
CREATE INDEX idx_code_inspector_thread_status ON code_inspector_thread(thread_status, updated_at);
CREATE INDEX idx_code_inspector_thread_operator ON code_inspector_thread(operator_id, thread_status);
CREATE INDEX idx_code_inspector_thread_lease ON code_inspector_thread(thread_status, lease_until);

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
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSING','DONE','FAILED')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    lease_until TEXT,
    worker_id TEXT,
    next_attempt_at TEXT,
    failure_kind TEXT CHECK (failure_kind IS NULL OR failure_kind IN ('RETRYABLE','NON_RETRYABLE','AMBIGUOUS')),
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO code_inspector_event_new (
    id, event_id, idempotency_key, issue_key, role, operator_id,
    agent_platform, runtime_backend, event_type, activity_id, stage_no,
    status, last_error, created_at, updated_at
)
SELECT id, event_id, idempotency_key, issue_key, role,
       CASE role WHEN 'developer' THEN 'codex-dev' ELSE 'codex-insp' END,
       'codex', 'codex-app-server', event_type, activity_id, stage_no,
       status, error_message, created_at, updated_at
FROM code_inspector_event;

DROP TABLE code_inspector_event;
ALTER TABLE code_inspector_event_new RENAME TO code_inspector_event;
CREATE INDEX idx_code_inspector_event_dispatch ON code_inspector_event(status, next_attempt_at, created_at, id);
CREATE INDEX idx_code_inspector_event_order ON code_inspector_event(issue_key, operator_id, status, id);
CREATE INDEX idx_code_inspector_event_lease ON code_inspector_event(status, lease_until);
