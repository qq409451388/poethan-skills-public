PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS review_task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT NOT NULL UNIQUE,
    project_name TEXT NOT NULL,
    project_path TEXT NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'IN_PROGRESS', 'CLOSED')),
    started_at TEXT,
    finished_at TEXT,
    close_reason TEXT,
    remark TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_review_task_project_name
    ON review_task(project_name);

CREATE INDEX IF NOT EXISTS idx_review_task_status
    ON review_task(status);

CREATE TABLE IF NOT EXISTS review_task_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    reason TEXT NOT NULL,
    source_issue_id INTEGER,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id, version_no),
    FOREIGN KEY(task_id) REFERENCES review_task(id) ON DELETE CASCADE,
    FOREIGN KEY(source_issue_id) REFERENCES review_issue(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_review_task_version_task
    ON review_task_version(task_id, version_no);

CREATE TABLE IF NOT EXISTS review_issue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_key TEXT NOT NULL UNIQUE,
    task_id INTEGER NOT NULL,
    introduced_version INTEGER NOT NULL,
    parent_issue_id INTEGER,
    title TEXT NOT NULL,
    dimension TEXT NOT NULL CHECK (
        dimension IN (
            'functional_correctness',
            'data_security',
            'stability_concurrency',
            'performance',
            'architecture_extensibility',
            'code_quality',
            'test_observability'
        )
    ),
    severity TEXT NOT NULL CHECK (
        severity IN ('critical', 'high', 'medium', 'low')
    ),
    remediation_benefit TEXT NOT NULL CHECK (
        remediation_benefit IN ('high', 'medium', 'low')
    ),
    remediation_cost TEXT NOT NULL CHECK (
        remediation_cost IN ('low', 'medium', 'high', 'extreme')
    ),
    disposition TEXT NOT NULL CHECK (
        disposition IN (
            'immediate_fix',
            'current_iteration',
            'near_term_iteration',
            'special_governance',
            'opportunistic_fix',
            'observe',
            'defer',
            'business_confirmation'
        )
    ),
    confidence TEXT NOT NULL CHECK (
        confidence IN ('high', 'medium', 'low')
    ),
    status TEXT NOT NULL CHECK (
        status IN (
            'PROPOSED',
            'IMPLEMENTED_PENDING_REVIEW',
            'CONFIRMED',
            'REDESIGN_REQUIRED',
            'CANCELLED'
        )
    ),
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
    FOREIGN KEY(task_id) REFERENCES review_task(id) ON DELETE CASCADE,
    FOREIGN KEY(parent_issue_id) REFERENCES review_issue(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_review_issue_task
    ON review_issue(task_id);

CREATE INDEX IF NOT EXISTS idx_review_issue_version
    ON review_issue(task_id, introduced_version);

CREATE INDEX IF NOT EXISTS idx_review_issue_status
    ON review_issue(status);

CREATE INDEX IF NOT EXISTS idx_review_issue_severity
    ON review_issue(severity);

CREATE INDEX IF NOT EXISTS idx_review_issue_dimension
    ON review_issue(dimension);

CREATE TABLE IF NOT EXISTS issue_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    activity_type TEXT NOT NULL CHECK (
        activity_type IN (
            'ISSUE_CREATED',
            'EVIDENCE_ADDED',
            'DESIGN_SUBMITTED',
            'IMPLEMENTATION_SUBMITTED',
            'REVIEW_APPROVED',
            'REVIEW_REJECTED',
            'REDESIGN_SUBMITTED',
            'VERIFICATION_PASSED',
            'VERIFICATION_FAILED',
            'VERIFICATION_EVIDENCE_ADDED',
            'STATUS_CHANGED',
            'COMMENT_ADDED'
        )
    ),
    operator_type TEXT NOT NULL CHECK (
        operator_type IN ('REVIEW_AGENT', 'DEVELOPMENT_AGENT', 'VERIFIER_AGENT', 'HUMAN', 'SYSTEM')
    ),
    operator_id TEXT NOT NULL,
    content TEXT NOT NULL,
    result_status TEXT,
    code_reference_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(issue_id) REFERENCES review_issue(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_issue_activity_issue
    ON issue_activity(issue_id, created_at);

CREATE TABLE IF NOT EXISTS agent_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_audit_log_agent
    ON agent_audit_log(agent_id, created_at);
