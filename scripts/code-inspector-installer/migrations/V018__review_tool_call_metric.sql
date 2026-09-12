-- Review DB CLI 的真实调用计数。只保存统计维度，不保存参数、输入或返回正文。

CREATE TABLE review_tool_call_metric (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    issue_key TEXT,
    role TEXT NOT NULL CHECK (role IN ('inspector', 'developer', 'human')),
    operator_id TEXT NOT NULL,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_review_tool_call_metric_issue
    ON review_tool_call_metric(issue_key, created_at, id);
CREATE INDEX idx_review_tool_call_metric_created
    ON review_tool_call_metric(created_at, command, id);
