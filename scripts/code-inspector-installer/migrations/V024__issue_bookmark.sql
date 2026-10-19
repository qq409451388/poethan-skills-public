-- Human 书签：任务列表与待我处理优先展示；纯标记，不影响状态机与流程排序语义。
ALTER TABLE review_issue ADD COLUMN bookmarked INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_review_issue_bookmarked ON review_issue (bookmarked, progress);
