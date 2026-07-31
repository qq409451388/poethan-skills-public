CREATE INDEX IF NOT EXISTS idx_issue_activity_created
    ON issue_activity(created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_issue_activity_type_created
    ON issue_activity(activity_type, created_at DESC, id DESC);
