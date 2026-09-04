-- migration: foreign_keys_off
-- 为复杂 Issue 增加独立、可追踪、串行执行的 Stage Plan，并扩展 Stage 活动类型。

CREATE TABLE issue_stage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    plan_no INTEGER NOT NULL CHECK (plan_no >= 1),
    stage_no INTEGER NOT NULL CHECK (stage_no >= 1),
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL,
    plan_status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (plan_status IN ('ACTIVE', 'SUPERSEDED')),
    status TEXT NOT NULL CHECK (
        status IN ('PLANNED', 'IN_PROGRESS', 'PENDING_REVIEW', 'APPROVED', 'SUPERSEDED')
    ),
    submitted_commit_sha TEXT,
    developer_summary TEXT,
    test_evidence_json TEXT NOT NULL DEFAULT '[]',
    code_reference_json TEXT NOT NULL DEFAULT '[]',
    submission_metadata_json TEXT NOT NULL DEFAULT '{}',
    review_comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    submitted_at TEXT,
    approved_at TEXT,
    UNIQUE(issue_id, plan_no, stage_no),
    FOREIGN KEY(issue_id) REFERENCES review_issue(id) ON DELETE CASCADE
);

CREATE INDEX idx_issue_stage_issue_plan
    ON issue_stage(issue_id, plan_no, stage_no);

CREATE INDEX idx_issue_stage_status
    ON issue_stage(plan_status, status, updated_at DESC);

CREATE TABLE issue_activity_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    activity_type TEXT NOT NULL CHECK (activity_type IN (
        'ISSUE_CREATED', 'EVIDENCE_ADDED',
        'DESIGN_REQUESTED', 'DESIGN_GUIDANCE', 'DESIGN_SUBMITTED', 'DESIGN_APPROVED', 'DESIGN_REJECTED',
        'STAGE_PLAN_CREATED', 'STAGE_SUBMITTED', 'STAGE_APPROVED', 'STAGE_REJECTED', 'STAGE_PLAN_SUPERSEDED',
        'HUMAN_CONFIRMATION_REQUESTED', 'HUMAN_CONFIRMATION_PROVIDED',
        'IMPLEMENTATION_SUBMITTED', 'REVIEW_APPROVED', 'REVIEW_REJECTED', 'REDESIGN_SUBMITTED',
        'INSPECTOR_CONFIRMATION_PROVIDED', 'VERIFICATION_PASSED', 'VERIFICATION_FAILED',
        'VERIFICATION_EVIDENCE_ADDED', 'STATUS_CHANGED', 'COMMENT_ADDED'
    )),
    operator_type TEXT NOT NULL CHECK (
        operator_type IN ('INSPECTOR_AGENT', 'DEVELOPMENT_AGENT', 'VERIFIER_AGENT', 'HUMAN', 'SYSTEM')
    ),
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
