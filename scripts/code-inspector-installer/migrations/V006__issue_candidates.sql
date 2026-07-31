CREATE TABLE IF NOT EXISTS issue_candidate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_key TEXT NOT NULL UNIQUE,
    task_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    facts TEXT NOT NULL,
    rationale TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    suggested_dimension TEXT CHECK (
        suggested_dimension IS NULL OR suggested_dimension IN (
            'functional_correctness',
            'data_security',
            'stability_concurrency',
            'performance',
            'architecture_extensibility',
            'code_quality',
            'test_observability'
        )
    ),
    suggested_severity TEXT CHECK (
        suggested_severity IS NULL OR suggested_severity IN ('critical', 'high', 'medium', 'low')
    ),
    suggested_confidence TEXT CHECK (
        suggested_confidence IS NULL OR suggested_confidence IN ('high', 'medium', 'low')
    ),
    status TEXT NOT NULL DEFAULT 'SUBMITTED' CHECK (
        status IN ('SUBMITTED', 'UNDER_REVIEW', 'ACCEPTED', 'REJECTED')
    ),
    submitted_by TEXT NOT NULL,
    reviewed_by TEXT,
    review_comment TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES review_task(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_issue_candidate_task_status
    ON issue_candidate(task_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_issue_candidate_updated
    ON issue_candidate(updated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_review_issue_updated
    ON review_issue(updated_at DESC, id DESC);
