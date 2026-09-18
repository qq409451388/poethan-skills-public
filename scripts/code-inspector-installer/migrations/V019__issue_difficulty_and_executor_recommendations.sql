-- Issue 的抽象困难程度与 Developer 推荐执行配置。
-- difficulty 由 Inspector 输出；recommended_executors_json 由本机 Model Router 计算，
-- 两者都是附加字段，不参与 Issue 主状态机。

ALTER TABLE review_issue ADD COLUMN difficulty INTEGER;
ALTER TABLE review_issue ADD COLUMN difficulty_reason_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE review_issue ADD COLUMN recommended_executors_json TEXT NOT NULL DEFAULT '[]';

-- difficulty 属于 Working Set 治理字段，变更后必须推进 projection_revision。
DROP TRIGGER trg_review_issue_projection_update;

CREATE TRIGGER trg_review_issue_projection_update
AFTER UPDATE OF title, summary, expected_outcome, technical_note, status,
                dimension, severity, current_attempt_no, evidence_json, local_terms_json,
                difficulty
ON review_issue
WHEN NEW.projection_revision = OLD.projection_revision
BEGIN
    UPDATE review_issue
    SET projection_revision = OLD.projection_revision + 1
    WHERE id = NEW.id;
END;
