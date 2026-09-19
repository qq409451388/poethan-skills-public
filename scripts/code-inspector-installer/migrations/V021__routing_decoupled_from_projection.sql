-- 把 Routing 推荐彻底移出 Issue 核心 Projection。
--
-- 背景：V020 的触发器把 recommended_executors_json 也算作 Working Set 字段，
-- 而 issue-get 在缓存失效时会回写该列，导致「单纯读取 Issue」就推进了
-- projection_revision，把旁路信息混进了核心业务状态。
--
-- V021 起：
--   * recommendedExecutors 改为读取时动态投影（difficulty + 当前 Routing 配置），不再落库；
--   * projection_revision 只反映核心 Working Set / Workflow 字段；
--   * assignment_json 仍是 Inspector / Human 的显式业务决定，保留在触发器内。
--
-- 旧列 recommended_executors_json / routing_revision 保留但不再作为业务真相，
-- 因此不需要高风险的数据迁移。

DROP TRIGGER trg_review_issue_projection_update;

CREATE TRIGGER trg_review_issue_projection_update
AFTER UPDATE OF title, summary, expected_outcome, technical_note, status,
                dimension, severity, current_attempt_no, evidence_json, local_terms_json,
                difficulty, assignment_json
ON review_issue
WHEN NEW.projection_revision = OLD.projection_revision
BEGIN
    UPDATE review_issue
    SET projection_revision = OLD.projection_revision + 1
    WHERE id = NEW.id;
END;
