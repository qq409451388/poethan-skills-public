ALTER TABLE review_task ADD COLUMN review_level TEXT CHECK (review_level IN ('L1', 'L2', 'L3'));
ALTER TABLE review_task ADD COLUMN review_scope TEXT;
ALTER TABLE review_task ADD COLUMN baseline_ref TEXT;
ALTER TABLE review_task ADD COLUMN scope_fingerprint TEXT;

CREATE INDEX IF NOT EXISTS idx_review_task_active_identity
    ON review_task(project_path, scope_fingerprint, status);

ALTER TABLE review_issue ADD COLUMN dedupe_key TEXT;

CREATE INDEX IF NOT EXISTS idx_review_issue_active_dedupe
    ON review_issue(task_id, dedupe_key, status);
