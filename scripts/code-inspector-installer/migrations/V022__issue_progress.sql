-- Inspector 评估的完成进度，与流程状态独立；历史 Issue 保持待评估。
ALTER TABLE review_issue ADD COLUMN progress INTEGER CHECK (progress BETWEEN 0 AND 100);
ALTER TABLE review_issue ADD COLUMN progress_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE review_issue ADD COLUMN progress_updated_at TEXT;
ALTER TABLE review_issue ADD COLUMN progress_updated_by TEXT;
