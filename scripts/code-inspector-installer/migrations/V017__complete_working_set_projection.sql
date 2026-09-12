-- 补齐 V016 发布后加入 Working Set 的字段；不修改已发布 migration。

DROP TRIGGER trg_review_issue_projection_update;

CREATE TRIGGER trg_review_issue_projection_update
AFTER UPDATE OF title, summary, expected_outcome, technical_note, status,
                dimension, severity, current_attempt_no, evidence_json, local_terms_json
ON review_issue
WHEN NEW.projection_revision = OLD.projection_revision
BEGIN
    UPDATE review_issue
    SET projection_revision = OLD.projection_revision + 1
    WHERE id = NEW.id;
END;

DROP TRIGGER trg_review_task_projection_update;

CREATE TRIGGER trg_review_task_projection_update
AFTER UPDATE OF task_key, project_name, review_level, review_scope, baseline_ref
ON review_task
BEGIN
    UPDATE review_issue
    SET projection_revision = projection_revision + 1
    WHERE task_id = NEW.id;
END;
