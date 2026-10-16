-- 讨论按 Issue 下的话题组织；旧记录不伪造 AI 摘要。
CREATE TABLE issue_discussion_topic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES review_issue(id) ON DELETE CASCADE,
    category TEXT NOT NULL CHECK(category IN ('GENERAL','DESIGN','IMPLEMENTATION','BLOCKER','REVIEW','VERIFICATION','SCOPE')),
    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 160),
    attempt_no INTEGER,
    plan_no INTEGER,
    stage_no INTEGER,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(issue_id, id)
);
CREATE INDEX idx_discussion_topic_issue ON issue_discussion_topic(issue_id, id);
ALTER TABLE issue_discussion ADD COLUMN human_summary TEXT;
ALTER TABLE issue_discussion ADD COLUMN topic_id INTEGER REFERENCES issue_discussion_topic(id);
ALTER TABLE issue_activity ADD COLUMN human_summary TEXT;
ALTER TABLE issue_decision ADD COLUMN human_summary TEXT;
ALTER TABLE issue_activity ADD COLUMN topic_id INTEGER REFERENCES issue_discussion_topic(id);
ALTER TABLE issue_discussion_revision ADD COLUMN previous_summary TEXT;
ALTER TABLE issue_discussion_revision ADD COLUMN replacement_summary TEXT;
ALTER TABLE issue_activity_revision ADD COLUMN previous_summary TEXT;
ALTER TABLE issue_activity_revision ADD COLUMN replacement_summary TEXT;

INSERT INTO issue_discussion_topic(issue_id, category, title, created_by, created_at)
SELECT issue_id, topic,
       CASE topic WHEN 'DESIGN' THEN '历史方案讨论' WHEN 'IMPLEMENTATION' THEN '历史实现讨论'
                  WHEN 'VERIFICATION' THEN '历史验证讨论' ELSE '历史一般讨论' END,
       'migration', MIN(created_at)
FROM issue_discussion GROUP BY issue_id, topic;
UPDATE issue_discussion SET topic_id = (
    SELECT t.id FROM issue_discussion_topic t WHERE t.issue_id=issue_discussion.issue_id
        AND t.category=issue_discussion.topic LIMIT 1
);

-- 正式交付仍只存一份正文，通过外键加入讨论。
INSERT INTO issue_discussion_topic(issue_id, category, title, attempt_no, plan_no, stage_no, created_by, created_at)
SELECT issue_id, CASE WHEN activity_type IN ('DESIGN_SUBMITTED','REDESIGN_SUBMITTED') THEN 'DESIGN' ELSE 'IMPLEMENTATION' END,
       CASE WHEN activity_type IN ('DESIGN_SUBMITTED','REDESIGN_SUBMITTED') THEN '历史设计提交'
            WHEN activity_type='STAGE_SUBMITTED' THEN '历史阶段提交' ELSE '历史实现提交' END,
       attempt_no, json_extract(metadata_json,'$.plan_no'), json_extract(metadata_json,'$.stage_no'),
       operator_id, created_at
FROM issue_activity WHERE activity_type IN ('DESIGN_SUBMITTED','REDESIGN_SUBMITTED','STAGE_SUBMITTED','IMPLEMENTATION_SUBMITTED');
UPDATE issue_activity SET topic_id = (
    SELECT t.id FROM issue_discussion_topic t WHERE t.issue_id=issue_activity.issue_id
      AND t.attempt_no=issue_activity.attempt_no AND t.created_at=issue_activity.created_at
      AND t.category=CASE WHEN issue_activity.activity_type IN ('DESIGN_SUBMITTED','REDESIGN_SUBMITTED') THEN 'DESIGN' ELSE 'IMPLEMENTATION' END
      AND t.plan_no IS json_extract(issue_activity.metadata_json,'$.plan_no')
      AND t.stage_no IS json_extract(issue_activity.metadata_json,'$.stage_no') LIMIT 1
) WHERE activity_type IN ('DESIGN_SUBMITTED','REDESIGN_SUBMITTED','STAGE_SUBMITTED','IMPLEMENTATION_SUBMITTED');

-- 字段变化日志与技术正文分离；只写发生变化的字段，不记录读取或无变化写入。
CREATE TABLE issue_change (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES review_issue(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_issue_change_issue ON issue_change(issue_id, created_at, id);

CREATE TRIGGER issue_discussion_topic_insert BEFORE INSERT ON issue_discussion
WHEN NEW.topic_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM issue_discussion_topic WHERE id=NEW.topic_id AND issue_id=NEW.issue_id
) BEGIN SELECT RAISE(ABORT, 'topic does not belong to issue'); END;

CREATE TRIGGER issue_discussion_topic_update BEFORE UPDATE ON issue_discussion
WHEN NEW.topic_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM issue_discussion_topic WHERE id=NEW.topic_id AND issue_id=NEW.issue_id
) BEGIN SELECT RAISE(ABORT, 'topic does not belong to issue'); END;

CREATE TRIGGER issue_activity_topic_insert BEFORE INSERT ON issue_activity
WHEN NEW.topic_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM issue_discussion_topic WHERE id=NEW.topic_id AND issue_id=NEW.issue_id
) BEGIN SELECT RAISE(ABORT, 'topic does not belong to issue'); END;

CREATE TRIGGER issue_activity_topic_update BEFORE UPDATE ON issue_activity
WHEN NEW.topic_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM issue_discussion_topic WHERE id=NEW.topic_id AND issue_id=NEW.issue_id
) BEGIN SELECT RAISE(ABORT, 'topic does not belong to issue'); END;

CREATE TRIGGER collaboration_issue_title_changed AFTER UPDATE OF title ON review_issue
WHEN OLD.title IS NOT NEW.title
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','title','before',OLD.title,'after',NEW.title));
END;

CREATE TRIGGER collaboration_issue_summary_changed AFTER UPDATE OF summary ON review_issue
WHEN OLD.summary IS NOT NEW.summary
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','summary','before',OLD.summary,'after',NEW.summary));
END;

CREATE TRIGGER collaboration_issue_expected_outcome_changed AFTER UPDATE OF expected_outcome ON review_issue
WHEN OLD.expected_outcome IS NOT NEW.expected_outcome
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','expected_outcome','before',OLD.expected_outcome,'after',NEW.expected_outcome));
END;

CREATE TRIGGER collaboration_issue_technical_note_changed AFTER UPDATE OF technical_note ON review_issue
WHEN OLD.technical_note IS NOT NEW.technical_note
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','technical_note','before',OLD.technical_note,'after',NEW.technical_note));
END;

CREATE TRIGGER collaboration_issue_local_terms_json_changed AFTER UPDATE OF local_terms_json ON review_issue
WHEN OLD.local_terms_json IS NOT NEW.local_terms_json
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','local_terms_json','before',OLD.local_terms_json,'after',NEW.local_terms_json));
END;

CREATE TRIGGER collaboration_issue_status_changed AFTER UPDATE OF status ON review_issue
WHEN OLD.status IS NOT NEW.status
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','status','before',OLD.status,'after',NEW.status));
END;

CREATE TRIGGER collaboration_issue_current_attempt_no_changed AFTER UPDATE OF current_attempt_no ON review_issue
WHEN OLD.current_attempt_no IS NOT NEW.current_attempt_no
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','current_attempt_no','before',OLD.current_attempt_no,'after',NEW.current_attempt_no));
END;

CREATE TRIGGER collaboration_issue_severity_changed AFTER UPDATE OF severity ON review_issue
WHEN OLD.severity IS NOT NEW.severity
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','severity','before',OLD.severity,'after',NEW.severity));
END;

CREATE TRIGGER collaboration_issue_dimension_changed AFTER UPDATE OF dimension ON review_issue
WHEN OLD.dimension IS NOT NEW.dimension
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','dimension','before',OLD.dimension,'after',NEW.dimension));
END;

CREATE TRIGGER collaboration_issue_disposition_changed AFTER UPDATE OF disposition ON review_issue
WHEN OLD.disposition IS NOT NEW.disposition
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','disposition','before',OLD.disposition,'after',NEW.disposition));
END;

CREATE TRIGGER collaboration_issue_difficulty_changed AFTER UPDATE OF difficulty ON review_issue
WHEN OLD.difficulty IS NOT NEW.difficulty
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','difficulty','before',OLD.difficulty,'after',NEW.difficulty));
END;

CREATE TRIGGER collaboration_issue_assignment_json_changed AFTER UPDATE OF assignment_json ON review_issue
WHEN OLD.assignment_json IS NOT NEW.assignment_json
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','assignment_json','before',OLD.assignment_json,'after',NEW.assignment_json));
END;

CREATE TRIGGER collaboration_issue_progress_changed AFTER UPDATE OF progress ON review_issue
WHEN OLD.progress IS NOT NEW.progress
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','progress','before',OLD.progress,'after',NEW.progress));
END;

CREATE TRIGGER collaboration_issue_progress_reason_changed AFTER UPDATE OF progress_reason ON review_issue
WHEN OLD.progress_reason IS NOT NEW.progress_reason
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.id,'issue',NEW.id,'FIELD_CHANGED',json_object('field','progress_reason','before',OLD.progress_reason,'after',NEW.progress_reason));
END;

CREATE TRIGGER collaboration_stage_title_changed AFTER UPDATE OF title ON issue_stage
WHEN OLD.title IS NOT NEW.title
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'FIELD_CHANGED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'field','title','before',OLD.title,'after',NEW.title));
END;

CREATE TRIGGER collaboration_stage_objective_changed AFTER UPDATE OF objective ON issue_stage
WHEN OLD.objective IS NOT NEW.objective
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'FIELD_CHANGED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'field','objective','before',OLD.objective,'after',NEW.objective));
END;

CREATE TRIGGER collaboration_stage_acceptance_criteria_changed AFTER UPDATE OF acceptance_criteria ON issue_stage
WHEN OLD.acceptance_criteria IS NOT NEW.acceptance_criteria
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'FIELD_CHANGED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'field','acceptance_criteria','before',OLD.acceptance_criteria,'after',NEW.acceptance_criteria));
END;

CREATE TRIGGER collaboration_stage_status_changed AFTER UPDATE OF status ON issue_stage
WHEN OLD.status IS NOT NEW.status
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'FIELD_CHANGED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'field','status','before',OLD.status,'after',NEW.status));
END;

CREATE TRIGGER collaboration_stage_plan_status_changed AFTER UPDATE OF plan_status ON issue_stage
WHEN OLD.plan_status IS NOT NEW.plan_status
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'FIELD_CHANGED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'field','plan_status','before',OLD.plan_status,'after',NEW.plan_status));
END;

CREATE TRIGGER collaboration_stage_planned_change_scope_json_changed AFTER UPDATE OF planned_change_scope_json ON issue_stage
WHEN OLD.planned_change_scope_json IS NOT NEW.planned_change_scope_json
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'FIELD_CHANGED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'field','planned_change_scope_json','before',OLD.planned_change_scope_json,'after',NEW.planned_change_scope_json));
END;

CREATE TRIGGER collaboration_stage_change_reason_changed AFTER UPDATE OF change_reason ON issue_stage
WHEN OLD.change_reason IS NOT NEW.change_reason
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'FIELD_CHANGED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'field','change_reason','before',OLD.change_reason,'after',NEW.change_reason));
END;

CREATE TRIGGER collaboration_stage_protected_behaviors_json_changed AFTER UPDATE OF protected_behaviors_json ON issue_stage
WHEN OLD.protected_behaviors_json IS NOT NEW.protected_behaviors_json
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'FIELD_CHANGED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'field','protected_behaviors_json','before',OLD.protected_behaviors_json,'after',NEW.protected_behaviors_json));
END;

CREATE TRIGGER collaboration_stage_submitted_commit_sha_changed AFTER UPDATE OF submitted_commit_sha ON issue_stage
WHEN OLD.submitted_commit_sha IS NOT NEW.submitted_commit_sha
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'FIELD_CHANGED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'field','submitted_commit_sha','before',OLD.submitted_commit_sha,'after',NEW.submitted_commit_sha));
END;

CREATE TRIGGER collaboration_stage_diff_summary_changed AFTER UPDATE OF diff_summary ON issue_stage
WHEN OLD.diff_summary IS NOT NEW.diff_summary
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'FIELD_CHANGED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'field','diff_summary','before',OLD.diff_summary,'after',NEW.diff_summary));
END;

CREATE TRIGGER collaboration_stage_baseline_status_changed AFTER UPDATE OF baseline_status ON issue_stage
WHEN OLD.baseline_status IS NOT NEW.baseline_status
BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'FIELD_CHANGED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'field','baseline_status','before',OLD.baseline_status,'after',NEW.baseline_status));
END;

CREATE TRIGGER collaboration_stage_created AFTER INSERT ON issue_stage BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(NEW.issue_id,'stage',NEW.id,'STAGE_CREATED',json_object('plan_no',NEW.plan_no,'stage_no',NEW.stage_no,'title',NEW.title));
END;
CREATE TRIGGER collaboration_stage_deleted BEFORE DELETE ON issue_stage
WHEN EXISTS(SELECT 1 FROM review_issue WHERE id=OLD.issue_id) BEGIN
    INSERT INTO issue_change(issue_id,entity_type,entity_id,action,changes_json)
    VALUES(OLD.issue_id,'stage',OLD.id,'STAGE_DELETED',json_object('plan_no',OLD.plan_no,'stage_no',OLD.stage_no,'title',OLD.title));
END;
