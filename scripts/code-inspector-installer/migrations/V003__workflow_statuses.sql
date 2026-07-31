-- migration: foreign_keys_off
-- SQLite 无法原地修改 CHECK 约束；重建三张带状态约束的表，并完整复制历史数据。

CREATE TABLE review_task_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT NOT NULL UNIQUE,
    project_name TEXT NOT NULL,
    project_path TEXT NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'IN_PROGRESS', 'ON_HOLD', 'BLOCKED', 'CLOSED', 'CANCELLED')),
    started_at TEXT,
    finished_at TEXT,
    close_reason TEXT,
    remark TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    review_level TEXT CHECK (review_level IN ('L1', 'L2', 'L3')),
    review_scope TEXT,
    baseline_ref TEXT,
    scope_fingerprint TEXT
);
INSERT INTO review_task_new SELECT * FROM review_task;
DROP TABLE review_task;
ALTER TABLE review_task_new RENAME TO review_task;
CREATE INDEX idx_review_task_project_name ON review_task(project_name);
CREATE INDEX idx_review_task_status ON review_task(status);
CREATE INDEX idx_review_task_active_identity ON review_task(project_path, scope_fingerprint, status);

CREATE TABLE review_issue_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_key TEXT NOT NULL UNIQUE,
    task_id INTEGER NOT NULL,
    introduced_version INTEGER NOT NULL,
    parent_issue_id INTEGER,
    title TEXT NOT NULL,
    dimension TEXT NOT NULL CHECK (dimension IN ('functional_correctness', 'data_security', 'stability_concurrency', 'performance', 'architecture_extensibility', 'code_quality', 'test_observability')),
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    remediation_benefit TEXT NOT NULL CHECK (remediation_benefit IN ('high', 'medium', 'low')),
    remediation_cost TEXT NOT NULL CHECK (remediation_cost IN ('low', 'medium', 'high', 'extreme')),
    disposition TEXT NOT NULL CHECK (disposition IN ('immediate_fix', 'current_iteration', 'near_term_iteration', 'special_governance', 'opportunistic_fix', 'observe', 'defer', 'business_confirmation')),
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    status TEXT NOT NULL CHECK (status IN ('PROPOSED', 'IN_PROGRESS', 'ON_HOLD', 'BLOCKED', 'REVIEWER_CONFIRMATION_REQUIRED', 'IMPLEMENTED_PENDING_REVIEW', 'REDESIGN_REQUIRED', 'CONFIRMED', 'CANCELLED')),
    description TEXT NOT NULL,
    facts TEXT NOT NULL,
    trigger_conditions_json TEXT NOT NULL DEFAULT '[]',
    potential_impact_json TEXT NOT NULL DEFAULT '[]',
    impact_scope_json TEXT NOT NULL DEFAULT '[]',
    rationale TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    estimated_change_json TEXT NOT NULL DEFAULT '{}',
    current_attempt_no INTEGER NOT NULL DEFAULT 0,
    confirmed_at TEXT,
    cancelled_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dedupe_key TEXT,
    FOREIGN KEY(task_id) REFERENCES review_task(id) ON DELETE CASCADE,
    FOREIGN KEY(parent_issue_id) REFERENCES review_issue(id) ON DELETE SET NULL
);
INSERT INTO review_issue_new SELECT * FROM review_issue;
DROP TABLE review_issue;
ALTER TABLE review_issue_new RENAME TO review_issue;
CREATE INDEX idx_review_issue_task ON review_issue(task_id);
CREATE INDEX idx_review_issue_version ON review_issue(task_id, introduced_version);
CREATE INDEX idx_review_issue_status ON review_issue(status);
CREATE INDEX idx_review_issue_severity ON review_issue(severity);
CREATE INDEX idx_review_issue_dimension ON review_issue(dimension);
CREATE INDEX idx_review_issue_active_dedupe ON review_issue(task_id, dedupe_key, status);

CREATE TABLE issue_activity_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    activity_type TEXT NOT NULL CHECK (activity_type IN ('ISSUE_CREATED', 'EVIDENCE_ADDED', 'DESIGN_SUBMITTED', 'IMPLEMENTATION_SUBMITTED', 'REVIEW_APPROVED', 'REVIEW_REJECTED', 'REDESIGN_SUBMITTED', 'REVIEWER_CONFIRMATION_PROVIDED', 'VERIFICATION_PASSED', 'VERIFICATION_FAILED', 'VERIFICATION_EVIDENCE_ADDED', 'STATUS_CHANGED', 'COMMENT_ADDED')),
    operator_type TEXT NOT NULL CHECK (operator_type IN ('REVIEW_AGENT', 'DEVELOPMENT_AGENT', 'VERIFIER_AGENT', 'HUMAN', 'SYSTEM')),
    operator_id TEXT NOT NULL,
    content TEXT NOT NULL,
    result_status TEXT,
    code_reference_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(issue_id) REFERENCES review_issue(id) ON DELETE CASCADE
);
INSERT INTO issue_activity_new SELECT * FROM issue_activity;
DROP TABLE issue_activity;
ALTER TABLE issue_activity_new RENAME TO issue_activity;
CREATE INDEX idx_issue_activity_issue ON issue_activity(issue_id, created_at);
