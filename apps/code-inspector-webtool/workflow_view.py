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
        ("STAGE_IN_PROGRESS", "阶段开发中", "Developer", "按当前阶段范围实施修改，完成后提交阶段验收。"),
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
    ("STAGE_SCOPE_DECLARED", "STAGE_IN_PROGRESS"), ("STAGE_IN_PROGRESS", "STAGE_SUBMITTED"), ("STAGE_SUBMITTED", "STAGE_APPROVED"),
    ("STAGE_SUBMITTED", "STAGE_REJECTED"), ("STAGE_REJECTED", "STAGE_IN_PROGRESS"),
    ("STAGE_APPROVED", "STAGE_IN_PROGRESS"), ("STAGE_APPROVED", "IMPLEMENTED_PENDING_REVIEW"),
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


def workflow_graph(issue, activities, stages, current_execution_stage=None, discussions=None):
    nodes = []
    for row, (lane, entries) in enumerate(LANES):
        for col, (key, title, owner, description) in enumerate(entries):
            nodes.append(dict(key=key, title=title, owner=owner, description=description,
                              lane=lane, x=30 + col * 232, y=52 + row * 160))
    by_key = {node["key"]: node for node in nodes}
    visited, traversed = set(), set()
    previous = None
    trail = []

    def record(key):
        nonlocal previous
        if key not in by_key:
            return
        visited.add(key)
        if not trail or trail[-1] != key:
            trail.append(key)
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
        if stage.get("prepared_at") or stage.get("submitted_at"):
            visited.add("STAGE_IN_PROGRESS")
        if stage.get("submitted_at"):
            visited.add("STAGE_SUBMITTED")
        if stage.get("status") == "APPROVED":
            visited.add("STAGE_APPROVED")
    current = issue["status"]
    if current == "IN_PROGRESS" and current_execution_stage:
        current = ("STAGE_SUBMITTED" if current_execution_stage["status"] == "PENDING_REVIEW"
                   else "STAGE_IN_PROGRESS" if current_execution_stage.get("prepared_at")
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
    result = {"nodes": nodes, "edges": edges, "lanes": [dict(title=title, y=22 + row*160) for row, (title, _) in enumerate(LANES)]}
    result.update(reading_route(issue, activities, stages, current_execution_stage, current, visited, by_key, discussions or []))
    result["diagrams"] = diagram_sections(by_key, current, paused, traversed)
    result["diagram_secondary_open"] = any(diagram["open"] and diagram["secondary"] for diagram in result["diagrams"])
    result["connected_diagram"] = connected_diagram(result["diagrams"], traversed, current, paused)
    graph = result["connected_diagram"]
    result["waiting_on"] = issue["status"] in {"INSPECTOR_CONFIRMATION_REQUIRED", "HUMAN_CONFIRMATION_REQUIRED", "BLOCKED", "ON_HOLD"}
    result["paused_stage"] = current_execution_stage if result["waiting_on"] else None
    if result["paused_stage"]:
        stage = result["paused_stage"]
        anchor = ("STAGE_SUBMITTED" if stage["status"] == "PENDING_REVIEW"
                  else "STAGE_IN_PROGRESS" if stage.get("prepared_at") else "STAGE_PLAN_CREATED")
        for node in graph["nodes"]:
            node["waiting_here"] = node["key"] == anchor
    result["stage_context"] = None
    if current_execution_stage:
        stage = current_execution_stage
        prior = [item["stage_no"] for item in stages
                 if item.get("plan_no") == stage.get("plan_no")
                 and item.get("stage_no", 0) < stage.get("stage_no", 0)
                 and item["status"] == "APPROVED"]
        arrival = None
        for activity in reversed(activities):
            metadata = activity.get("metadata") or {}
            if metadata.get("plan_no") != stage.get("plan_no"):
                continue
            if activity["activity_type"] == "STAGE_REJECTED" and metadata.get("stage_no") == stage.get("stage_no"):
                arrival = "rework"
                break
            if activity["activity_type"] == "STAGE_APPROVED" and prior and metadata.get("stage_no") == max(prior):
                arrival = "next_stage"
                break
        if arrival is None and prior:
            arrival = "next_stage"
        development_label = {"rework": "驳回返工中", "next_stage": "下一阶段开发中"}.get(arrival, "阶段开发中")
        if stage["status"] == "PENDING_REVIEW":
            reason = "本阶段已提交，等待验收。"
        elif arrival == "rework":
            reason = "最近一次进入开发的原因：本阶段验收驳回，当前正在返工。"
        elif arrival == "next_stage":
            reason = "前序阶段 " + "、".join(map(str, sorted(prior))) + " 已通过，当前进入下一阶段开发。"
        else:
            reason = "当前阶段正在开发，尚未提交验收。"
        result["stage_context"] = {"title": f"Stage {stage.get('stage_no')} · {stage.get('title', '')}", "reason": reason}
        for node in graph["nodes"]:
            if node["key"] == "STAGE_IN_PROGRESS" and (node["current"] or node.get("waiting_here")):
                node["title"] = f"Stage {stage.get('stage_no')} · {development_label}"
        if stage["status"] == "IN_PROGRESS":
            for edge in graph["edges"]:
                if edge["target"] == "STAGE_IN_PROGRESS" and edge["source"] in {"STAGE_REJECTED", "STAGE_APPROVED"}:
                    edge["current_entry"] = edge["source"] == ("STAGE_REJECTED" if arrival == "rework" else "STAGE_APPROVED" if arrival == "next_stage" else None)
                    if edge["current_entry"]:
                        edge["label"] = "本次：" + edge["label"]
    graph["hover_paths"] = hover_paths(graph, trail)
    # 正常前进线与悬浮路径采用同一依据，不能因中间夹着其他动作而显示为未走过。
    recorded_forward_edges = {edge for path in graph["hover_paths"].values() for edge in path["edges"]}
    for edge in graph["edges"]:
        edge["visited"] = edge["visited"] or f"{edge['source']}|{edge['target']}" in recorded_forward_edges

    configure_flow_mode(graph, stages, trail)
    result["flow_mode_label"] = "分阶段流程：创建计划 → 启动首阶段 → 逐阶段验收" if graph["staged"] else "直接开发流程：开发修复 → 提交整体实现 → 最终审核"
    return result


def configure_flow_mode(graph, stages, trail):
    """显示 Issue 实际采用的流程；底层完整节点保留历史事实与路径索引。"""
    staged = any(stage.get("plan_status", "ACTIVE") == "ACTIVE" for stage in stages)
    graph["staged"] = staged
    hidden = {"IN_PROGRESS"} if staged else {
        "STAGE_PLAN_CREATED", "STAGE_SCOPE_DECLARED", "STAGE_IN_PROGRESS",
        "STAGE_SUBMITTED", "STAGE_APPROVED", "STAGE_REJECTED"}
    graph["display_nodes"] = [node for node in graph["nodes"] if node["key"] not in hidden]
    graph["display_edges"] = [dict(edge) for edge in graph["edges"]
                              if edge["source"] not in hidden and edge["target"] not in hidden]
    if staged:
        plan = next(node for node in graph["nodes"] if node["key"] == "STAGE_PLAN_CREATED")
        graph["display_edges"].append({
            "source": "DESIGN_APPROVED", "target": "STAGE_PLAN_CREATED",
            "path": "M1220,146 H1340 V365 H440 V448",
            "label": "方案通过 → 创建计划并启动 Stage 1", "label_x": 900, "label_y": 356,
            "visited": plan["visited"], "cross_layer": True})
        for source, x, bottom in [("REVIEW_REJECTED", 195, 1200), ("VERIFICATION_FAILED", 505, 1220)]:
            graph["display_edges"].append({"source": source, "target": "STAGE_IN_PROGRESS",
                "path": f"M{x},1150 V{bottom} H55 V550 H685 V574",
                "label": "返回阶段开发", "label_x": 350, "label_y": bottom-9,
                "visited": False, "cross_layer": True})
    graph["hover_paths"] = hover_paths({"nodes": graph["display_nodes"], "edges": graph["display_edges"]}, trail)
    graph["layers"][1]["title"] = "分阶段开发与验收" if staged else "直接开发（无 Stage Plan）"


# 主视图按人阅读的顺序展示；完成标记仍只消费原有事实，不由位置补齐历史。
ROUTE_STEPS = [
    ("discovery", "记录问题", {"PROPOSED"}, {"PROPOSED"}),
    ("design", "对齐修复方案", {"DESIGN_REQUIRED", "DESIGN_PENDING_REVIEW", "DESIGN_APPROVED", "DESIGN_REJECTED", "REDESIGN_REQUIRED", "REDESIGN_SUBMITTED"}, {"DESIGN_APPROVED"}),
    ("development", "开发与阶段验收", {"IN_PROGRESS", "STAGE_PLAN_CREATED", "STAGE_SCOPE_DECLARED", "STAGE_IN_PROGRESS", "STAGE_SUBMITTED", "STAGE_APPROVED", "STAGE_REJECTED"}, {"IMPLEMENTED_PENDING_REVIEW"}),
    ("review", "审核最终实现", {"IMPLEMENTED_PENDING_REVIEW", "REVIEW_APPROVED", "REVIEW_REJECTED"}, {"REVIEW_APPROVED"}),
    ("verification", "最终验证", {"VERIFICATION_PASSED", "VERIFICATION_FAILED"}, {"VERIFICATION_PASSED"}),
    ("closure", "确认关闭", {"CONFIRMED"}, {"CONFIRMED"}),
]


def reading_route(issue, activities, stages, current_stage, current, visited, by_key, discussions):
    status = issue["status"]
    active_stages = sorted(
        (stage for stage in stages if stage.get("plan_status", "ACTIVE") == "ACTIVE"),
        key=lambda stage: (stage.get("plan_no", 0), stage.get("stage_no", 0)),
    )
    route = []
    current_group = next((key for key, _, keys, _ in ROUTE_STEPS if current in keys), None)
    # 等待确认/暂停是覆盖状态，保留事实上的最近主流程位置，不将它当作已通过。
    if current_group is None:
        for activity in reversed(activities):
            event = EVENT_NODES.get(activity["activity_type"], activity["activity_type"])
            group = next((key for key, _, keys, _ in ROUTE_STEPS if event in keys), None)
            if group:
                current_group = group
                break
    current_index = next((i for i, step in enumerate(ROUTE_STEPS) if step[0] == current_group), -1)
    descriptions = {
        "discovery": "核实问题、影响与完成标准。",
        "design": "明确修复边界，由审核方确认方案。",
        "development": "完成修改，并逐阶段提交和验收。" if active_stages else "完成代码修改与必要测试，再提交整体实现。",
        "review": "审核完整实现，确认问题与修复范围一致。",
        "verification": "验证实际效果，保留可复查的证据。",
        "closure": "明确满足完成标准后，确认关闭 Issue。",
    }
    for index, (key, title, keys, completed_by) in enumerate(ROUTE_STEPS):
        if key == "design" and not (visited & keys):
            continue
        recorded = bool(visited & keys)
        done = (any(activity["activity_type"] == "IMPLEMENTATION_SUBMITTED" for activity in activities)
                if key == "development" else bool(visited & completed_by))
        is_current = key == current_group and status not in {"CONFIRMED", "CANCELLED"}
        state = "current" if is_current else "done" if done else "recorded" if recorded else "missing" if index < current_index else "pending"
        labels = {"current": "当前环节", "done": "已完成", "recorded": "有处理记录", "missing": "未记录", "pending": "待进行"}
        if key == "development" and done:
            labels["done"] = "已提交整体实现"
        route.append({"key": key, "title": title, "description": descriptions[key], "state": state,
                      "state_label": labels[state], "current": is_current})
    stage_cards = []
    stage_labels = {"APPROVED": "验收通过", "PENDING_REVIEW": "待验收", "IN_PROGRESS": "进行中", "PLANNED": "待开始"}
    for stage in active_stages:
        is_current = bool(current_stage and stage.get("id") == current_stage.get("id")
                          and stage.get("stage_no") == current_stage.get("stage_no")
                          and stage["status"] in {"IN_PROGRESS", "PENDING_REVIEW"})
        stage_cards.append({"stage_no": stage.get("stage_no"), "title": stage.get("title", ""),
                            "state": "done" if stage["status"] == "APPROVED" else "current" if is_current else "pending",
                            "status_label": stage_labels.get(stage["status"], stage["status"])})
    branch_kinds = {"DESIGN_REJECTED", "STAGE_REJECTED", "REVIEW_REJECTED", "VERIFICATION_FAILED", "REDESIGN_REQUIRED", "STAGE_PLAN_SUPERSEDED",
                    "HUMAN_CONFIRMATION_REQUIRED", "HUMAN_CONFIRMATION_PROVIDED", "INSPECTOR_CONFIRMATION_REQUIRED", "INSPECTOR_CONFIRMATION_PROVIDED", "ON_HOLD", "BLOCKED", "CANCELLED"}
    branches = []
    for activity in activities:
        event = activity["activity_type"]
        if event == "STATUS_CHANGED":
            event = activity.get("result_status")
        elif event == "HUMAN_CONFIRMATION_REQUESTED":
            event = "HUMAN_CONFIRMATION_REQUIRED"
        if event not in branch_kinds:
            continue
        metadata = activity.get("metadata") or {}
        title = by_key[event]["title"]
        if metadata.get("stage_no") is not None:
            title = f"Stage {metadata['stage_no']} · {title}"
        branches.append({"title": title, "time": activity.get("created_at"),
                         "summary": activity.get("human_summary") or by_key[event]["description"]})
    current_title = by_key[current]["title"]
    if current_stage and status == "IN_PROGRESS":
        current_title = f"Stage {current_stage.get('stage_no', '')} · {current_stage.get('title', current_title)}"
    summary_records = [record for record in [*activities, *discussions] if record.get("human_summary")]
    latest_summary = max(summary_records, key=lambda record: record.get("amended_at") or record.get("created_at") or "").get("human_summary") if summary_records else ""
    next_step = {
        "PROPOSED": "确认是否需要先设计方案，再开始开发。",
        "DESIGN_REQUIRED": "Developer 提交具体方案，交由 Inspector 审核。",
        "DESIGN_PENDING_REVIEW": "Inspector 审核方案，通过后进入开发。",
        "IMPLEMENTED_PENDING_REVIEW": "Inspector 审核当前整体实现，并完成后续验证。",
        "INSPECTOR_CONFIRMATION_REQUIRED": "由 Inspector 确认当前技术边界，再继续处理。",
        "HUMAN_CONFIRMATION_REQUIRED": "由 Human 处理待决事项，再恢复工作流。",
        "ON_HOLD": "等待恢复处理；搁置不代表已经解决。",
        "BLOCKED": "先解决当前依赖或阻断项，再恢复处理。",
        "REDESIGN_REQUIRED": "先修订方案和边界，再重新审核。",
        "CONFIRMED": "该 Issue 已确认关闭；可在协作记录中追查结论。",
        "CANCELLED": "该 Issue 已取消，不代表修复验证通过。",
    }.get(status, "完成当前修改与验证后，提交整体实现供审核。")
    if status == "IN_PROGRESS" and current_stage:
        next_step = ("Inspector 验收当前阶段；通过后再继续后续阶段。" if current_stage["status"] == "PENDING_REVIEW"
                     else "完成当前阶段的验收要求，再提交阶段结果。")
    return {"route": route, "stage_cards": stage_cards, "branches": list(reversed(branches)),
            "current_title": current_title, "current_summary": latest_summary, "next_step": next_step}


def diagram_sections(by_key, current, paused, traversed):
    """各区只有局部连线；回线使用节点外的专用上/下通道。"""
    specs = [
        {"key": "design", "title": "方案与设计", "note": "简单问题可以直接进入开发；方案通过后进入开发区。", "height": 270,
         "positions": {"PROPOSED": (20,48), "DESIGN_REQUIRED": (330,48), "DESIGN_PENDING_REVIEW": (640,48), "DESIGN_APPROVED": (950,48), "DESIGN_REJECTED": (640,174)},
         "edges": [
             ("PROPOSED", "DESIGN_REQUIRED", "M210,86 H330", "", 0,0),
             ("DESIGN_REQUIRED", "DESIGN_PENDING_REVIEW", "M520,86 H640", "",0,0),
             ("DESIGN_PENDING_REVIEW", "DESIGN_APPROVED", "M830,86 H950", "通过",885,77),
             ("DESIGN_PENDING_REVIEW", "DESIGN_REJECTED", "M735,124 V174", "未通过",772,151),
             ("DESIGN_REJECTED", "DESIGN_REQUIRED", "M640,212 H425 V124", "修订后重审",525,203),
         ]},
        {"key": "stages", "title": "开发与阶段验收", "note": "无阶段计划时直接开发并提交整体实现；分阶段时逐阶段验收，全部通过后进入最终审核区。", "height": 270,
         "positions": {"IN_PROGRESS": (20,48), "STAGE_PLAN_CREATED": (265,48), "STAGE_SCOPE_DECLARED": (510,48), "STAGE_IN_PROGRESS": (510,174), "STAGE_SUBMITTED": (755,174), "STAGE_APPROVED": (1000,174), "STAGE_REJECTED": (755,300)},
         "edges": [
             ("IN_PROGRESS", "STAGE_PLAN_CREATED", "M210,86 H265", "",0,0),
             ("STAGE_PLAN_CREATED", "STAGE_SCOPE_DECLARED", "M455,86 H510", "",0,0),
             ("STAGE_SCOPE_DECLARED", "STAGE_IN_PROGRESS", "M605,124 V174", "",0,0),
             ("STAGE_IN_PROGRESS", "STAGE_SUBMITTED", "M700,212 H755", "",0,0),
             ("STAGE_SUBMITTED", "STAGE_APPROVED", "M945,212 H1000", "通过",972,203),
             ("STAGE_SUBMITTED", "STAGE_REJECTED", "M850,250 V300", "未通过",888,277),
             ("STAGE_REJECTED", "STAGE_IN_PROGRESS", "M755,338 H605 V250", "同一 Stage 驳回 → 返工",680,329),
             ("STAGE_APPROVED", "STAGE_IN_PROGRESS", "M1095,174 V150 H605 V174", "前一 Stage 通过 → 下一 Stage",850,141),
         ]},
        {"key": "review", "title": "最终审核、验证与关闭", "note": "审核或验证未通过，返回开发修复；确认关闭以实际记录为准。", "height": 270,
         "positions": {"IMPLEMENTED_PENDING_REVIEW": (20,48), "REVIEW_APPROVED": (330,48), "VERIFICATION_PASSED": (640,48), "CONFIRMED": (950,48), "REVIEW_REJECTED": (20,174), "VERIFICATION_FAILED": (330,174)},
         "edges": [
             ("IMPLEMENTED_PENDING_REVIEW", "REVIEW_APPROVED", "M210,86 H330", "通过",270,77),
             ("REVIEW_APPROVED", "VERIFICATION_PASSED", "M520,86 H640", "验证通过",580,77),
             ("VERIFICATION_PASSED", "CONFIRMED", "M830,86 H950", "确认",890,77),
             ("IMPLEMENTED_PENDING_REVIEW", "REVIEW_REJECTED", "M115,124 V174", "审核未通过",170,151),
             ("REVIEW_APPROVED", "VERIFICATION_FAILED", "M425,124 V174", "验证未通过",480,151),
         ]},
    ]
    secondary = [
        ("redesign", "重设计", ["REDESIGN_REQUIRED", "STAGE_PLAN_SUPERSEDED", "DESIGN_REQUIRED", "REDESIGN_SUBMITTED", "DESIGN_PENDING_REVIEW"], "方案方向失效时保留旧计划历史，重新设计并审核。"),
        ("technical", "技术边界确认", ["INSPECTOR_CONFIRMATION_REQUIRED", "INSPECTOR_CONFIRMATION_PROVIDED", "IN_PROGRESS"], "技术边界确认后，按实际状态回到开发。"),
        ("human", "人工决定", ["HUMAN_CONFIRMATION_REQUIRED", "HUMAN_CONFIRMATION_PROVIDED", "IN_PROGRESS"], "人工决定也可以转为重新设计、搁置、受阻或取消，以实际决定为准。"),
        ("pauses", "暂停与终止", ["ON_HOLD", "BLOCKED", "CANCELLED"], "搁置和受阻可在条件满足后恢复处理；取消不代表修复通过。"),
        ("evidence", "补充验证证据", ["VERIFICATION_EVIDENCE_ADDED", "VERIFICATION_PASSED"], "补充证据后仍需实际验证通过，不能由补录动作推定通过。"),
        ("fastmode", "FastMode 检查结果", ["FASTMODE_PASS", "FASTMODE_FAIL"], "人工协调的快速检查，只记录检查结果，不自动关闭 Issue。"),
    ]
    for key,title,keys,note in secondary:
        positions = {node_key:(20+index*245,48) for index,node_key in enumerate(keys)}
        edges = []
        if key not in {"pauses", "fastmode"}:
            for source,target in zip(keys,keys[1:]):
                x = positions[source][0]+190
                edges.append((source,target,f"M{x},86 H{positions[target][0]}","",0,0))
        specs.append({"key":key,"title":title,"positions":positions,"edges":edges,"note":note,"height":160})
    current_sections = [spec for spec in specs if current in spec["positions"]]
    # 通用开发节点在恢复分支也出现，优先主流程；暂停/确认只展开对应分支。
    primary = next((spec["key"] for spec in current_sections), "stages")
    results = []
    for spec in specs:
        nodes = [{**by_key[key], "x":x, "y":y} for key,(x,y) in spec["positions"].items()]
        edges = [{"source":source,"target":target,"path":path,"label":label,"label_x":x,"label_y":y,
                  "visited":(source,target) in traversed,
                  "flowing":source == current and not paused and (source,target) in PATHS}
                 for source,target,path,label,x,y in spec["edges"]]
        results.append({**spec,"nodes":nodes,"edges":edges,"secondary":spec["key"] not in {"design", "stages", "review"},"open":spec["key"] == primary,
                        "visited":sum(node["visited"] for node in nodes)})
    return results


def connected_diagram(diagrams, traversed, current, paused):
    """三个主流程层共用坐标系；跨层前进走右侧，返回开发走左侧。"""
    import re
    nodes, edges, layers = [], [], []
    for diagram, offset_y in zip(diagrams[:3], (60, 400, 900)):
        offset_x = 80
        layers.append({"title": diagram["title"], "y": offset_y+17,
                       "line_y": offset_y+33})
        nodes.extend({**node, "x": node["x"]+offset_x, "y": node["y"]+offset_y} for node in diagram["nodes"])
        for edge in diagram["edges"]:
            def translate(match):
                command, raw = match.groups()
                values = [float(value) for value in raw.split(',')]
                if command == 'M':
                    return f"M{values[0]+offset_x:g},{values[1]+offset_y:g}"
                return f"{command}{values[0]+(offset_x if command == 'H' else offset_y):g}"
            path = re.sub(r"([MHV])([\d.,]+)", translate, edge["path"])
            edges.append({**edge, "path": path, "label_x": edge["label_x"]+offset_x,
                          "label_y": edge["label_y"]+offset_y})
    cross_layer = [
        ("DESIGN_APPROVED", "IN_PROGRESS", "M1220,146 H1340 V365 H195 V448", "方案通过，进入开发", 1020,356),
        ("STAGE_APPROVED", "IMPLEMENTED_PENDING_REVIEW", "M1270,612 H1360 V865 H195 V948", "全部阶段完成 → 提交整体实现", 1010,856),
        ("PROPOSED", "IN_PROGRESS", "M195,184 V340 H55 V486 H100", "无需设计，直接开发", 225,331),
        ("IN_PROGRESS", "IMPLEMENTED_PENDING_REVIEW", "M195,524 V840 H80 V986 H100", "不分阶段时，直接提交整体实现", 335,831),
        ("REVIEW_REJECTED", "IN_PROGRESS", "M195,1150 V1200 H55 V486 H100", "审核未通过，返回开发", 330,1191),
        ("VERIFICATION_FAILED", "IN_PROGRESS", "M505,1150 V1220 H30 V486 H100", "验证未通过，返回开发", 670,1211),
    ]
    for source,target,path,label,x,y in cross_layer:
        edges.append({"source":source,"target":target,"path":path,"label":label,"label_x":x,"label_y":y,
                      "visited":(source,target) in traversed,
                      "flowing":source == current and not paused and (source,target) in PATHS,
                      "cross_layer":True})
    return {"nodes":nodes,"edges":edges,"layers":layers,"width":1390,"height":1260}


def hover_paths(graph, trail):
    """仅高亮已记录的正常前进路径；返工和下一阶段回线不参与祖先回溯。"""
    result = {}
    for node in graph["nodes"]:
        key = node["key"]
        arrivals = [index for index, event in enumerate(trail) if event == key]
        if not arrivals and not node.get("visited"):
            result[key] = {"nodes": [key], "edges": []}
            continue
        # 计划创建可能先于进入开发落库；按正常流程方向回溯，不能用时间前缀截断。
        observed = {item["key"] for item in graph["nodes"] if item.get("visited")} | set(trail)
        candidates = []
        for edge in graph["edges"]:
            source, target = edge["source"], edge["target"]
            if source not in observed or target not in observed:
                continue
            if (source, target) == ("PROPOSED", "IN_PROGRESS") and "DESIGN_REQUIRED" in observed:
                continue
            if (source, target) == ("IN_PROGRESS", "IMPLEMENTED_PENDING_REVIEW") and "STAGE_PLAN_CREATED" in observed:
                continue
            if (source, target) in {
                ("DESIGN_REJECTED", "DESIGN_REQUIRED"),
                ("STAGE_REJECTED", "STAGE_IN_PROGRESS"),
                ("STAGE_APPROVED", "STAGE_IN_PROGRESS"),
                ("REVIEW_REJECTED", "IN_PROGRESS"),
                ("VERIFICATION_FAILED", "IN_PROGRESS"),
                ("REVIEW_REJECTED", "STAGE_IN_PROGRESS"),
                ("VERIFICATION_FAILED", "STAGE_IN_PROGRESS"),
            }:
                continue
            candidates.append(edge)
        # 回溯正常流程的祖先；只使用有实际记录的节点，已排除所有回线。
        ancestors = {key}
        changed = True
        while changed:
            changed = False
            for edge in candidates:
                if edge["target"] in ancestors and edge["source"] not in ancestors:
                    ancestors.add(edge["source"])
                    changed = True
        result[key] = {"nodes": sorted(ancestors),
                       "edges": [f"{edge['source']}|{edge['target']}" for edge in candidates
                                 if edge["source"] in ancestors and edge["target"] in ancestors]}
    return result
