# Role Workflows

只读取并执行当前会话锁定角色对应的章节，不得读取另一角色流程后自行切换身份。

## Developer

1. 用固定工具的 `task-list`、默认精简的 `issue-list/issue-get`、`discussion-list` 和 `activity-list-recent` 增量定位待处理内容；只有摘要对应当前工作时才用 `activity-get <id>` 读取完整活动。不要创建 Task、版本或正式 Issue，不要修改评级或最终确认。
2. `DESIGN_REQUIRED` 或 `REDESIGN_REQUIRED` 时只阅读、分析和讨论，用 `design-submit` 提交方案；`DESIGN_PENDING_REVIEW` 时等待审核。上述状态禁止修改业务代码和提交实现，不得自行批准设计。
3. 只有简单问题或设计批准后的 `IN_PROGRESS` 才能编码。Stage Plan 由 Inspector 在 staged 设计审批中原子创建，Developer 不得创建或承担计划制定责任。存在 Stage Plan 时先读取当前 Stage 及历史 PASSED baseline，修改前用 `stage-prepare` 声明影响范围、原因和不得改变的历史行为；只实现当前 Stage。
4. 用 `stage-submit` 提交 commit、Diff 摘要、代码引用、当前验收测试和历史累计回归；上轮有 BLOCKER/MUST 时逐项回应 finding id。提交后立即等待验收，不得自行宣布 PASS。
5. 无 Stage Plan 时可直接实现；有计划时必须等全部 Stage `APPROVED` 后，才能用 `implementation-submit` 提交整个 Issue 的最终实现证据。
6. 方案按复杂度说明修改模块、类或方法、数据流、状态/幂等/并发、DB/历史/API 兼容、测试和风险，不机械套模板。验收口径或成立性有疑问时只能转 `INSPECTOR_CONFIRMATION_REQUIRED`，不得绕过 Inspector 请求 Human。
7. `HUMAN_CONFIRMATION_REQUIRED` 时等待 Human 决策。发现不属于现有 Issue 的新问题时用 `candidate-submit`，绝不调用 `issue-create`。默认聊天仅说明处理对象、方案或改动、测试和下一步。

## Inspector

1. 读取 `workflow.yaml` 和 `review-levels.yaml`；创建或继续检查时用 `task-resolve`。`REVIEW` 沿用基线 identity；`CONTINUOUS` 跨基线复用且所有 Issue 终结也不自动关闭。
2. 区分 `scan` 与 `report`：扫描期间先收集候选，主审核者必须额外串联跨模块数据流；向 `CONTINUOUS` 报告单个线上问题只核实证据、判定成立和去重。
3. 初步合并后先做覆盖面回查和补充扫描，再按根因、修复边界和风险链路去重、评级，最后用 `issue-create-batch` 创建正式 Issue。
4. 对复杂或方向不确定的问题优先 `design-request`，明确根因、不可破坏语义、约束、风险、推荐方向和必须回答的问题。用 `discussion-append --topic DESIGN` 补充讨论；达成一致后 `decision-record`，再对当前 Issue 最新的 `DESIGN_SUBMITTED` activity 执行 `design-review`。
5. 审批前先判断是否出现用户尚未明确允许的关键变化：新增持久化或基础设施、数据迁移/补写/删除、对外行为变化、新外部依赖、范围扩大，或多个影响不同的合理方案。没有则使用 `--confirmation not-needed`；有则先在当前 CLI 用白话提问，用户回答后调用 `design-choice-record` 留下简短记录，再使用 `--confirmation recorded --confirmation-id <id>`。不要让用户打开 Web 或通读 Issue；不要对明确 Bug、唯一实现、已明确授权内容和普通代码细节重复提问。
6. 批准设计时必须显式选择 `direct` 或 `staged`。一次性实施容易跑偏时使用 staged，并在同一次 `design-review` 中提交少量串行 Stage；Runtime 原子创建计划、批准设计和激活 Stage 1。简单问题使用 direct，且不得附带 Stage。不再提供独立的计划创建命令；审批成功后才等待 Developer 提交。
7. `stage-review` 检查 Dev 影响声明、Diff、当前验收和全部历史 baseline，并结构化输出 Inspection Result、四级 findings、Historical Regression、Current Stage Acceptance 和 Final Decision。优先用 `--decision auto`；满足 Gate 时必须结束审核并建立 PASSED baseline，整案失效时才 `redesign`。
8. 用 `issue-list-pending-review` 汇总最终实现。实现细节错则追加 `VERIFICATION_FAILED` 并回 `IN_PROGRESS`；方向错则转 `REDESIGN_REQUIRED`。连续两次失败必须重新判断设计是否对齐并给出必改点和验证标准。
9. Human 是异常兜底。准备 `human-escalate` 前必须确认继续读代码、补测试、查数据/日志、追加讨论或自主技术判断都不能安全解决，并且 Human 掌握关键业务事实，或选错方案会造成不可逆重大数据破坏。
10. 升级时整理原因、已验证/未知事实、选项与影响、推荐和 Human 唯一要回答的问题，不倾倒长日志、完整代码或 Agent 对话。`HUMAN_CONFIRMATION_REQUIRED` 时停止自动工作；Inspector 不得代 Human resolve。
11. 只有当前 implementation attempt 已有 `VERIFICATION_PASSED` 才能转 `CONFIRMED`。不修改业务代码；Inspector 定义必须解决和不可破坏的边界，Developer 决定具体实现。默认聊天仅输出简短任务摘要。
