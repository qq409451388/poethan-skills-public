"""工作流只读视图：按事实亮灯，不从当前状态推断历史步骤。"""

LANES = [
    ("问题与设计", [
        ("PROPOSED", "发现问题", "Inspector", "核实问题并创建 Issue。"),
        ("DESIGN_REQUIRED", "设计指导", "Inspector", "明确根因、约束、验收要求。"),
        ("DESIGN_PENDING_REVIEW", "方案待审核", "Developer → Inspector", "提交具体方案并等待审核。"),
        ("DESIGN_APPROVED", "方案批准", "Inspector / Human", "选择直接实施或分阶段实施。"),
        ("DESIGN_REJECTED", "方案驳回", "Inspector / Human", "补充或修正方案后重新提交。"),
    ]),
    ("实现与阶段", [
        ("IN_PROGRESS", "开发修复", "Developer", "直接实现或按已批准的 Stage 开发修复。"),
        ("STAGE_PLAN_CREATED", "创建阶段计划", "Inspector", "批准分阶段方案并激活首阶段。"),
        ("STAGE_SCOPE_DECLARED", "声明阶段范围", "Developer", "声明影响范围与历史保护项。"),
        ("STAGE_SUBMITTED", "阶段提交待验收", "Developer → Inspector", "提交阶段实现与验证证据。"),
        ("STAGE_APPROVED", "阶段验收通过", "Inspector", "建立 PASSED baseline，进入下一阶段或最终提交。"),
    ]),
    ("审核与验证", [
        ("IMPLEMENTED_PENDING_REVIEW", "最终实现待审核", "Developer → Inspector", "所有必要实现完成后提交最终结果。"),
        ("REVIEW_APPROVED", "实现审核通过", "Inspector / Human", "代码与边界审核通过，继续验证。"),
        ("VERIFICATION_PASSED", "最终验证通过", "Inspector", "完成本轮验证并保留证据。"),
        ("CONFIRMED", "确认关闭", "Inspector / Human", "验证通过后确认；人工可覆盖状态，实际记录为准。"),
        ("VERIFICATION_EVIDENCE_ADDED", "补充验证证据", "Inspector", "补录必要的验证事实。"),
    ]),
    ("返工与重设计", [
        ("REVIEW_REJECTED", "实现审核驳回", "Inspector / Human", "修复实现问题，重新提交审核。"),
        ("VERIFICATION_FAILED", "最终验证失败", "Inspector", "回到修复并补齐回归验证。"),
        ("STAGE_REJECTED", "阶段验收驳回", "Inspector", "修正当前阶段，再次准备与提交。"),
        ("REDESIGN_REQUIRED", "要求重新设计", "Inspector", "方向失效，必须修订架构指导。"),
        ("STAGE_PLAN_SUPERSEDED", "废弃旧阶段计划", "Inspector", "保留历史验收记录，建立修订方案。"),
    ]),
    ("确认与恢复", [
        ("INSPECTOR_CONFIRMATION_REQUIRED", "等待技术边界确认", "Developer → Inspector", "技术疑问由 Inspector 自主确认。"),
        ("INSPECTOR_CONFIRMATION_PROVIDED", "技术边界已确认", "Inspector", "提供结论后按实际状态继续。"),
        ("HUMAN_CONFIRMATION_REQUIRED", "等待人工最终决定", "Inspector → Human", "关键事实缺口或不可逆风险需人工决定，暂停自动流转。"),
        ("HUMAN_CONFIRMATION_PROVIDED", "人工决定已提供", "Human", "记录决定后重新设计、继续、搁置、阻塞或取消。"),
        ("REDESIGN_SUBMITTED", "修订方案已提交", "Developer", "重新提交方案，回到设计审核。"),
    ]),
    ("暂停与终止", [
        ("ON_HOLD", "暂时搁置", "Inspector / Developer / Human", "等待恢复，不代表已完成。"),
        ("BLOCKED", "受阻待协调", "Inspector / Developer / Human", "补充依赖、信息或协调后恢复。"),
        ("CANCELLED", "取消问题", "Inspector / Human", "终止当前 Issue；不代表修复验证通过。"),
        ("FASTMODE_PASS", "FastMode 检查通过", "Inspector", "只记录快速审核结果，不自动关闭 Issue。"),
        ("FASTMODE_FAIL", "FastMode 检查未通过", "Inspector", "记录未通过项，由 Human 协调后续。"),
    ]),
]

# 展示已支持的主要业务分支，不复制 Runtime 的角色权限或状态机校验。
PATHS = [
    ("PROPOSED", "DESIGN_REQUIRED"), ("PROPOSED", "IN_PROGRESS"),
    ("DESIGN_REQUIRED", "DESIGN_PENDING_REVIEW"),
    ("DESIGN_PENDING_REVIEW", "DESIGN_APPROVED"), ("DESIGN_PENDING_REVIEW", "DESIGN_REJECTED"),
    ("DESIGN_REJECTED", "DESIGN_REQUIRED"), ("DESIGN_APPROVED", "IN_PROGRESS"),
    ("DESIGN_APPROVED", "STAGE_PLAN_CREATED"), ("STAGE_PLAN_CREATED", "STAGE_SCOPE_DECLARED"),
    ("STAGE_SCOPE_DECLARED", "STAGE_SUBMITTED"), ("STAGE_SUBMITTED", "STAGE_APPROVED"),
    ("STAGE_SUBMITTED", "STAGE_REJECTED"), ("STAGE_REJECTED", "STAGE_SCOPE_DECLARED"),
    ("STAGE_APPROVED", "STAGE_SCOPE_DECLARED"), ("STAGE_APPROVED", "IMPLEMENTED_PENDING_REVIEW"),
    ("IN_PROGRESS", "IMPLEMENTED_PENDING_REVIEW"),
    ("IMPLEMENTED_PENDING_REVIEW", "REVIEW_APPROVED"), ("IMPLEMENTED_PENDING_REVIEW", "REVIEW_REJECTED"),
    ("REVIEW_REJECTED", "IN_PROGRESS"), ("REVIEW_APPROVED", "VERIFICATION_PASSED"),
    ("REVIEW_APPROVED", "VERIFICATION_FAILED"), ("VERIFICATION_FAILED", "IN_PROGRESS"),
    ("VERIFICATION_EVIDENCE_ADDED", "VERIFICATION_PASSED"), ("VERIFICATION_PASSED", "CONFIRMED"),
    ("IMPLEMENTED_PENDING_REVIEW", "REDESIGN_REQUIRED"), ("REDESIGN_REQUIRED", "STAGE_PLAN_SUPERSEDED"),
    ("REDESIGN_REQUIRED", "DESIGN_REQUIRED"), ("DESIGN_REQUIRED", "REDESIGN_SUBMITTED"),
    ("REDESIGN_SUBMITTED", "DESIGN_PENDING_REVIEW"),
    ("IN_PROGRESS", "INSPECTOR_CONFIRMATION_REQUIRED"),
    ("INSPECTOR_CONFIRMATION_REQUIRED", "INSPECTOR_CONFIRMATION_PROVIDED"),
    ("INSPECTOR_CONFIRMATION_PROVIDED", "IN_PROGRESS"),
    ("IN_PROGRESS", "HUMAN_CONFIRMATION_REQUIRED"),
    ("HUMAN_CONFIRMATION_REQUIRED", "HUMAN_CONFIRMATION_PROVIDED"),
    ("HUMAN_CONFIRMATION_PROVIDED", "DESIGN_REQUIRED"), ("HUMAN_CONFIRMATION_PROVIDED", "IN_PROGRESS"),
    ("IN_PROGRESS", "ON_HOLD"), ("ON_HOLD", "IN_PROGRESS"),
    ("IN_PROGRESS", "BLOCKED"), ("BLOCKED", "IN_PROGRESS"), ("IN_PROGRESS", "CANCELLED"),
]

EVENT_NODES = {"ISSUE_CREATED": "PROPOSED", "DESIGN_REQUESTED": "DESIGN_REQUIRED",
               "DESIGN_GUIDANCE": "DESIGN_REQUIRED", "DESIGN_SUBMITTED": "DESIGN_PENDING_REVIEW",
               "IMPLEMENTATION_SUBMITTED": "IMPLEMENTED_PENDING_REVIEW"}


def workflow_graph(issue, activities, stages, current_execution_stage=None):
    nodes = []
    for row, (lane, entries) in enumerate(LANES):
        for col, (key, title, owner, description) in enumerate(entries):
            nodes.append(dict(key=key, title=title, owner=owner, description=description,
                              lane=lane, x=30 + col * 232, y=52 + row * 160))
    by_key = {node["key"]: node for node in nodes}
    visited, traversed = set(), set()
    previous = None

    def record(key):
        nonlocal previous
        if key not in by_key:
            return
        visited.add(key)
        if previous and previous != key:
            traversed.add((previous, key))
        previous = key

    for activity in activities:
        kind = activity["activity_type"]
        metadata = activity.get("metadata") or {}
        if metadata.get("workflow_mode") == "FASTMODE" and kind in {"VERIFICATION_PASSED", "VERIFICATION_FAILED"}:
            record("FASTMODE_PASS" if kind == "VERIFICATION_PASSED" else "FASTMODE_FAIL")
        else:
            record(EVENT_NODES.get(kind, kind))
        # result_status 为实际落库状态；不推测未记录的中间环节。
        if not kind.startswith("STAGE_"):
            record(activity.get("result_status"))
    for stage in stages:
        if stage.get("prepared_at"):
            visited.add("STAGE_SCOPE_DECLARED")
        if stage.get("submitted_at"):
            visited.add("STAGE_SUBMITTED")
        if stage.get("status") == "APPROVED":
            visited.add("STAGE_APPROVED")
    current = issue["status"]
    if current == "IN_PROGRESS" and current_execution_stage:
        current = ("STAGE_SUBMITTED" if current_execution_stage["status"] == "PENDING_REVIEW"
                   else "STAGE_SCOPE_DECLARED" if current_execution_stage.get("prepared_at")
                   else "STAGE_PLAN_CREATED")
    visited.add(current)
    for node in nodes:
        node["visited"] = node["key"] in visited
        node["current"] = node["key"] == current
    edges = []
    paused = issue["status"] in {"ON_HOLD", "BLOCKED", "HUMAN_CONFIRMATION_REQUIRED", "INSPECTOR_CONFIRMATION_REQUIRED", "CONFIRMED", "CANCELLED"}
    for source, target in dict.fromkeys([*PATHS, *sorted(traversed)]):
        a, b = by_key[source], by_key[target]
        # 同行走中线，跨行走卡片间的空隙。
        x1, y1 = a["x"] + 200, a["y"] + 50
        x2, y2 = b["x"], b["y"] + 50
        if a["y"] == b["y"] and x2 > x1:
            path = f"M{x1},{y1} L{x2},{y2}"
        else:
            x1, y1 = a["x"] + 100, a["y"] + 100
            x2, y2 = b["x"] + 100, b["y"]
            bend = y1 + 24 if y2 > y1 else y1 + 36
            path = f"M{x1},{y1} V{bend} H{x2} V{y2}"
        edges.append(dict(source=source, target=target, path=path,
                          visited=(source, target) in traversed,
                          flowing=source == current and not paused and (source, target) in PATHS))
    return {"nodes": nodes, "edges": edges, "lanes": [dict(title=title, y=22 + row*160) for row, (title, _) in enumerate(LANES)]}
