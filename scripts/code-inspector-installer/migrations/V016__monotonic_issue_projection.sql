-- Working Set 使用 Issue 自身的单调版本；所有会改变投影内容的表写入由 DB 触发器推进版本。

ALTER TABLE review_issue ADD COLUMN projection_revision INTEGER NOT NULL DEFAULT 0;

-- 与 V015 以 MAX(activity.id) 作为临时版本的历史数据保持单调兼容。
UPDATE review_issue
SET projection_revision = COALESCE(
    (SELECT MAX(a.id) FROM issue_activity a WHERE a.issue_id = review_issue.id),
    0
);

ALTER TABLE code_inspector_event ADD COLUMN coalesced_through_row_id INTEGER;

CREATE TRIGGER trg_review_issue_projection_update
AFTER UPDATE OF title, summary, expected_outcome, technical_note, status,
                dimension, severity, current_attempt_no
ON review_issue
WHEN NEW.projection_revision = OLD.projection_revision
BEGIN
    UPDATE review_issue
    SET projection_revision = OLD.projection_revision + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER trg_review_task_projection_update
AFTER UPDATE OF task_key, project_name ON review_task
BEGIN
    UPDATE review_issue
    SET projection_revision = projection_revision + 1
    WHERE task_id = NEW.id;
END;

CREATE TRIGGER trg_issue_activity_projection_insert
AFTER INSERT ON issue_activity
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = NEW.issue_id;
END;

CREATE TRIGGER trg_issue_activity_projection_update
AFTER UPDATE ON issue_activity
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = NEW.issue_id;
END;

CREATE TRIGGER trg_issue_activity_projection_delete
AFTER DELETE ON issue_activity
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = OLD.issue_id;
END;

CREATE TRIGGER trg_issue_discussion_projection_insert
AFTER INSERT ON issue_discussion
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = NEW.issue_id;
END;

CREATE TRIGGER trg_issue_discussion_projection_update
AFTER UPDATE ON issue_discussion
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = NEW.issue_id;
END;

CREATE TRIGGER trg_issue_discussion_projection_delete
AFTER DELETE ON issue_discussion
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = OLD.issue_id;
END;

CREATE TRIGGER trg_issue_decision_projection_insert
AFTER INSERT ON issue_decision
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = NEW.issue_id;
END;

CREATE TRIGGER trg_issue_decision_projection_update
AFTER UPDATE ON issue_decision
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = NEW.issue_id;
END;

CREATE TRIGGER trg_issue_decision_projection_delete
AFTER DELETE ON issue_decision
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = OLD.issue_id;
END;

CREATE TRIGGER trg_issue_stage_projection_insert
AFTER INSERT ON issue_stage
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = NEW.issue_id;
END;

CREATE TRIGGER trg_issue_stage_projection_update
AFTER UPDATE ON issue_stage
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = NEW.issue_id;
END;

CREATE TRIGGER trg_issue_stage_projection_delete
AFTER DELETE ON issue_stage
BEGIN
    UPDATE review_issue SET projection_revision = projection_revision + 1 WHERE id = OLD.issue_id;
END;
