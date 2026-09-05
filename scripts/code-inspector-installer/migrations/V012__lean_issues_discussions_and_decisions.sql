-- 精简正式 Issue 的默认正文，并把讨论与生效结论从活动时间线中分离。

ALTER TABLE review_issue ADD COLUMN summary TEXT;
ALTER TABLE review_issue ADD COLUMN expected_outcome TEXT;
ALTER TABLE review_issue ADD COLUMN technical_note TEXT;
ALTER TABLE review_issue ADD COLUMN local_terms_json TEXT NOT NULL DEFAULT '{}';

UPDATE review_issue
SET summary = CASE
        WHEN TRIM(COALESCE(description, '')) != '' THEN description
        WHEN TRIM(COALESCE(facts, '')) != '' THEN facts
        ELSE title
    END,
    expected_outcome = COALESCE(expected_outcome, ''),
    technical_note = COALESCE(technical_note, '');

CREATE TABLE issue_discussion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    topic TEXT NOT NULL CHECK (topic IN ('GENERAL', 'DESIGN', 'IMPLEMENTATION', 'VERIFICATION')),
    operator_type TEXT NOT NULL CHECK (
        operator_type IN ('INSPECTOR_AGENT', 'DEVELOPMENT_AGENT', 'HUMAN', 'SYSTEM')
    ),
    operator_id TEXT NOT NULL,
    content TEXT NOT NULL,
    source_activity_id INTEGER UNIQUE,
    amended_at TEXT,
    amendment_count INTEGER NOT NULL DEFAULT 0 CHECK (amendment_count >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(issue_id) REFERENCES review_issue(id) ON DELETE CASCADE,
    FOREIGN KEY(source_activity_id) REFERENCES issue_activity(id) ON DELETE SET NULL
);

CREATE INDEX idx_issue_discussion_issue_created
    ON issue_discussion(issue_id, created_at, id);

CREATE TABLE issue_discussion_revision (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id INTEGER NOT NULL,
    revision_no INTEGER NOT NULL CHECK (revision_no >= 1),
    previous_content TEXT NOT NULL,
    replacement_content TEXT NOT NULL,
    reason TEXT,
    amended_by TEXT NOT NULL,
    amended_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(discussion_id, revision_no),
    FOREIGN KEY(discussion_id) REFERENCES issue_discussion(id) ON DELETE CASCADE
);

CREATE TABLE issue_decision (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    decision_type TEXT NOT NULL,
    scope_key TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL,
    operator_type TEXT NOT NULL CHECK (
        operator_type IN ('INSPECTOR_AGENT', 'DEVELOPMENT_AGENT', 'HUMAN', 'SYSTEM')
    ),
    operator_id TEXT NOT NULL,
    content TEXT NOT NULL,
    source_activity_id INTEGER UNIQUE,
    source_discussion_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    effective INTEGER NOT NULL DEFAULT 1 CHECK (effective IN (0, 1)),
    superseded_by_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(issue_id) REFERENCES review_issue(id) ON DELETE CASCADE,
    FOREIGN KEY(source_activity_id) REFERENCES issue_activity(id) ON DELETE SET NULL,
    FOREIGN KEY(superseded_by_id) REFERENCES issue_decision(id) ON DELETE SET NULL
);

CREATE INDEX idx_issue_decision_issue_effective
    ON issue_decision(issue_id, effective, created_at DESC, id DESC);

-- 遗留 COMMENT / DESIGN_GUIDANCE 迁入讨论区，之后不再出现在默认处理历史。
INSERT INTO issue_discussion(
    issue_id, topic, operator_type, operator_id, content, source_activity_id,
    amended_at, amendment_count, created_at
)
SELECT
    issue_id,
    CASE WHEN activity_type = 'DESIGN_GUIDANCE' THEN 'DESIGN' ELSE 'GENERAL' END,
    operator_type, operator_id, content, id, amended_at, amendment_count, created_at
FROM issue_activity
WHERE activity_type IN ('COMMENT_ADDED', 'DESIGN_GUIDANCE');

-- 把既有审核/验证结论建立为独立决定。活动仍保留作审计来源，但页面默认只展示决定。
INSERT INTO issue_decision(
    issue_id, attempt_no, decision_type, scope_key, outcome, operator_type, operator_id,
    content, source_activity_id, metadata_json, created_at
)
SELECT
    issue_id,
    attempt_no,
    CASE
        WHEN activity_type IN ('DESIGN_APPROVED', 'DESIGN_REJECTED') THEN 'DESIGN_REVIEW'
        WHEN activity_type IN ('STAGE_APPROVED', 'STAGE_REJECTED') THEN 'STAGE_REVIEW'
        WHEN activity_type IN ('REVIEW_APPROVED', 'REVIEW_REJECTED') THEN 'IMPLEMENTATION_REVIEW'
        WHEN activity_type IN ('VERIFICATION_PASSED', 'VERIFICATION_FAILED') THEN 'VERIFICATION'
        WHEN activity_type = 'INSPECTOR_CONFIRMATION_PROVIDED' THEN 'SCOPE_CONFIRMATION'
        WHEN activity_type = 'HUMAN_CONFIRMATION_PROVIDED' THEN 'HUMAN_CONFIRMATION'
        ELSE activity_type
    END,
    CASE
        WHEN activity_type IN ('STAGE_APPROVED', 'STAGE_REJECTED')
            THEN COALESCE(
                CAST(json_extract(metadata_json, '$.plan_no') AS TEXT) || ':' ||
                CAST(json_extract(metadata_json, '$.stage_no') AS TEXT),
                'activity:' || CAST(id AS TEXT)
            )
        WHEN activity_type IN ('VERIFICATION_PASSED', 'VERIFICATION_FAILED', 'REVIEW_APPROVED', 'REVIEW_REJECTED')
            THEN 'attempt:' || CAST(attempt_no AS TEXT)
        ELSE ''
    END,
    CASE
        WHEN activity_type IN ('DESIGN_APPROVED', 'STAGE_APPROVED', 'REVIEW_APPROVED', 'VERIFICATION_PASSED') THEN 'APPROVED'
        WHEN activity_type IN ('DESIGN_REJECTED', 'STAGE_REJECTED', 'REVIEW_REJECTED', 'VERIFICATION_FAILED') THEN 'REJECTED'
        ELSE COALESCE(result_status, 'PROVIDED')
    END,
    operator_type, operator_id, content, id, metadata_json, created_at
FROM issue_activity
WHERE activity_type IN (
    'DESIGN_APPROVED', 'DESIGN_REJECTED',
    'STAGE_APPROVED', 'STAGE_REJECTED',
    'REVIEW_APPROVED', 'REVIEW_REJECTED',
    'VERIFICATION_PASSED', 'VERIFICATION_FAILED',
    'INSPECTOR_CONFIRMATION_PROVIDED', 'HUMAN_CONFIRMATION_PROVIDED'
);

-- 同一决定类型和作用域默认只暴露最新结论；旧结论仍保留供审计展开。
UPDATE issue_decision
SET effective = 0
WHERE id NOT IN (
    SELECT MAX(id) FROM issue_decision GROUP BY issue_id, decision_type, scope_key
);
