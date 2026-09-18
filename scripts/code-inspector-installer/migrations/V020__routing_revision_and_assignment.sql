-- 推荐结果改为「可失效缓存」，并新增真正选定的执行者（assignment）。
--
-- 背景：recommended_executors_json 原来是创建时的一次性快照，Routing 配置变了以后
-- 历史 Issue 会一直沿用过期结果。V020 增加 routing_revision 作为缓存失效标记，
-- 读取时若与当前配置 revision 不一致就重新计算；assignment_json 单独保存人工/Inspector
-- 真正选定的执行配置，与「候选推荐」区分开。

ALTER TABLE review_issue ADD COLUMN routing_revision TEXT;
ALTER TABLE review_issue ADD COLUMN assignment_json TEXT NOT NULL DEFAULT '{}';

-- 推荐缓存与 assignment 都属于 Working Set 字段，变更后必须推进 projection_revision。
DROP TRIGGER trg_review_issue_projection_update;

CREATE TRIGGER trg_review_issue_projection_update
AFTER UPDATE OF title, summary, expected_outcome, technical_note, status,
                dimension, severity, current_attempt_no, evidence_json, local_terms_json,
                difficulty, recommended_executors_json, assignment_json
ON review_issue
WHEN NEW.projection_revision = OLD.projection_revision
BEGIN
    UPDATE review_issue
    SET projection_revision = OLD.projection_revision + 1
    WHERE id = NEW.id;
END;
