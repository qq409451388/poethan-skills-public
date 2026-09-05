-- migration: foreign_keys_off
-- 在现有 Stage 生命周期上增加开发前影响声明、结构化审核与递增验收基线。

ALTER TABLE issue_stage ADD COLUMN governance_version INTEGER NOT NULL DEFAULT 1
    CHECK (governance_version IN (1, 2));
ALTER TABLE issue_stage ADD COLUMN planned_change_scope_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE issue_stage ADD COLUMN change_reason TEXT;
ALTER TABLE issue_stage ADD COLUMN protected_behaviors_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE issue_stage ADD COLUMN prepared_at TEXT;
ALTER TABLE issue_stage ADD COLUMN diff_summary TEXT;
ALTER TABLE issue_stage ADD COLUMN resolved_findings_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE issue_stage ADD COLUMN review_round INTEGER NOT NULL DEFAULT 0 CHECK (review_round >= 0);
ALTER TABLE issue_stage ADD COLUMN review_findings_json TEXT NOT NULL DEFAULT '{"BLOCKER":[],"MUST":[],"SHOULD":[],"NIT":[]}';
ALTER TABLE issue_stage ADD COLUMN historical_regression_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE issue_stage ADD COLUMN current_acceptance_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE issue_stage ADD COLUMN baseline_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE issue_stage ADD COLUMN baseline_status TEXT CHECK (baseline_status IS NULL OR baseline_status = 'PASSED');
ALTER TABLE issue_stage ADD COLUMN baseline_established_at TEXT;

CREATE TABLE issue_activity_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    activity_type TEXT NOT NULL CHECK (activity_type IN (
        'ISSUE_CREATED', 'EVIDENCE_ADDED',
        'DESIGN_REQUESTED', 'DESIGN_GUIDANCE', 'DESIGN_SUBMITTED', 'DESIGN_APPROVED', 'DESIGN_REJECTED',
        'STAGE_PLAN_CREATED', 'STAGE_SCOPE_DECLARED', 'STAGE_SUBMITTED',
        'STAGE_APPROVED', 'STAGE_REJECTED', 'STAGE_PLAN_SUPERSEDED',
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
