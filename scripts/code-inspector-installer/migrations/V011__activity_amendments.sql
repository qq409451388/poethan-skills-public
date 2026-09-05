-- 允许 Developer / Inspector 修订自己活动的当前正文，同时把被替换版本移出活动主时间线保存。

ALTER TABLE issue_activity ADD COLUMN amended_at TEXT;
ALTER TABLE issue_activity ADD COLUMN amendment_count INTEGER NOT NULL DEFAULT 0
    CHECK (amendment_count >= 0);

CREATE TABLE issue_activity_revision (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL,
    revision_no INTEGER NOT NULL CHECK (revision_no >= 1),
    previous_content TEXT NOT NULL,
    replacement_content TEXT NOT NULL,
    reason TEXT,
    amended_by TEXT NOT NULL,
    amended_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(activity_id, revision_no),
    FOREIGN KEY(activity_id) REFERENCES issue_activity(id) ON DELETE CASCADE
);

CREATE INDEX idx_issue_activity_revision_activity
    ON issue_activity_revision(activity_id, revision_no);
