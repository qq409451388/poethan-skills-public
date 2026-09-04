-- migration: foreign_keys_off
-- 增加长期任务类型，并扩展设计审核状态/活动；保留所有历史主键与时间字段。

ALTER TABLE review_task ADD COLUMN task_type TEXT NOT NULL DEFAULT 'REVIEW'
    CHECK (task_type IN ('REVIEW', 'CONTINUOUS'));

CREATE INDEX idx_review_task_type_status
    ON review_task(task_type, status, updated_at DESC);

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
    status TEXT NOT NULL CHECK (status IN ('PROPOSED', 'DESIGN_REQUIRED', 'DESIGN_PENDING_REVIEW', 'IN_PROGRESS', 'ON_HOLD', 'BLOCKED', 'INSPECTOR_CONFIRMATION_REQUIRED', 'IMPLEMENTED_PENDING_REVIEW', 'REDESIGN_REQUIRED', 'CONFIRMED', 'CANCELLED')),
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
    FOREIGN KEY(parent_issue_id) REFERENCES review_issue_new(id) ON DELETE SET NULL
);

INSERT INTO review_issue_new (
    id, issue_key, task_id, introduced_version, parent_issue_id, title, dimension,
    severity, remediation_benefit, remediation_cost, disposition, confidence, status,
    description, facts, trigger_conditions_json, potential_impact_json, impact_scope_json,
    rationale, evidence_json, estimated_change_json, current_attempt_no, confirmed_at,
    cancelled_at, created_at, updated_at, dedupe_key
)
SELECT
    id, issue_key, task_id, introduced_version, parent_issue_id, title, dimension,
    severity, remediation_benefit, remediation_cost, disposition, confidence, status,
    description, facts, trigger_conditions_json, potential_impact_json, impact_scope_json,
    rationale, evidence_json, estimated_change_json, current_attempt_no, confirmed_at,
    cancelled_at, created_at, updated_at, dedupe_key
FROM review_issue;

DROP TABLE review_issue;
ALTER TABLE review_issue_new RENAME TO review_issue;

CREATE INDEX idx_review_issue_task ON review_issue(task_id);
CREATE INDEX idx_review_issue_version ON review_issue(task_id, introduced_version);
CREATE INDEX idx_review_issue_status ON review_issue(status);
CREATE INDEX idx_review_issue_severity ON review_issue(severity);
CREATE INDEX idx_review_issue_dimension ON review_issue(dimension);
CREATE INDEX idx_review_issue_active_dedupe ON review_issue(task_id, dedupe_key, status);
CREATE INDEX idx_review_issue_updated ON review_issue(updated_at DESC, id DESC);

CREATE TABLE issue_activity_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    activity_type TEXT NOT NULL CHECK (activity_type IN ('ISSUE_CREATED', 'EVIDENCE_ADDED', 'DESIGN_REQUESTED', 'DESIGN_GUIDANCE', 'DESIGN_SUBMITTED', 'DESIGN_APPROVED', 'DESIGN_REJECTED', 'IMPLEMENTATION_SUBMITTED', 'REVIEW_APPROVED', 'REVIEW_REJECTED', 'REDESIGN_SUBMITTED', 'INSPECTOR_CONFIRMATION_PROVIDED', 'VERIFICATION_PASSED', 'VERIFICATION_FAILED', 'VERIFICATION_EVIDENCE_ADDED', 'STATUS_CHANGED', 'COMMENT_ADDED')),
    operator_type TEXT NOT NULL CHECK (operator_type IN ('INSPECTOR_AGENT', 'DEVELOPMENT_AGENT', 'VERIFIER_AGENT', 'HUMAN', 'SYSTEM')),
    operator_id TEXT NOT NULL,
    content TEXT NOT NULL,
    result_status TEXT,
    code_reference_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(issue_id) REFERENCES review_issue(id) ON DELETE CASCADE
);

INSERT INTO issue_activity_new (
    id, issue_id, attempt_no, activity_type, operator_type, operator_id, content,
    result_status, code_reference_json, metadata_json, created_at
)
SELECT
    id, issue_id, attempt_no, activity_type, operator_type, operator_id, content,
    result_status, code_reference_json, metadata_json, created_at
FROM issue_activity;

DROP TABLE issue_activity;
ALTER TABLE issue_activity_new RENAME TO issue_activity;

CREATE INDEX idx_issue_activity_issue ON issue_activity(issue_id, created_at);
CREATE INDEX idx_issue_activity_created ON issue_activity(created_at DESC, id DESC);
CREATE INDEX idx_issue_activity_type_created ON issue_activity(activity_type, created_at DESC, id DESC);
