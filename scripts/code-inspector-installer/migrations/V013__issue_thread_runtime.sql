CREATE TABLE code_inspector_thread (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    issue_key TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('inspector', 'developer')),
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_active_at TEXT,
    FOREIGN KEY(issue_id) REFERENCES review_issue(id) ON DELETE CASCADE,
    UNIQUE(issue_key, role)
);

CREATE INDEX idx_code_inspector_thread_status ON code_inspector_thread(thread_status, updated_at);

CREATE TABLE code_inspector_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    issue_key TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('inspector', 'developer')),
    event_type TEXT NOT NULL,
    activity_id INTEGER,
    stage_no INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSING','DONE','FAILED')),
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_code_inspector_event_dispatch ON code_inspector_event(status, created_at, id);
