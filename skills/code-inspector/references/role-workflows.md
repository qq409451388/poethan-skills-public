# Role Workflows

只读取并执行当前会话锁定角色对应的章节，不得读取另一角色流程后自行切换身份。

## Developer

1. 已绑定 Issue 的 Action Turn 先且通常只调用一次 `issue-context-get`；只有 Working Set 摘要明确指向必要正文时才用 `discussion-get`、`activity-get` 或 `stage-history-get`。发现待处理 Issue 时才使用精简的列表命令。不要创建 Task、版本或正式 Issue，不要修改评级或最终确认。
2. `DESIGN_REQUIRED` 或 `REDESIGN_REQUIRED` 时只阅读、分析和讨论，用 `design-submit` 提交方案；`--summary` 写白话概要，`--scope-changes` 明确列出超出用户目标的变化（没有则传 `[]`），完整技术细节放 `--content`。方案在 Inspector 给出的 Root Cause、Architecture Direction、Boundaries、Design Questions、Acceptance 框架内决定具体 How，并逐项回答全部 Design Questions；发现 Architecture Direction 有事实错误或无法实现时，先用讨论或 `INSPECTOR_CONFIRMATION_REQUIRED` 反馈，不得静默改成另一套方向。`REDESIGN_REQUIRED` 下必须等 Inspector 用 `design-request` 修订架构指导后才能重交方案，Runtime 会直接拒绝抢跑。`DESIGN_PENDING_REVIEW` 时等待审核，不得编码或自行批准。
3. 只有简单问题或设计批准后的 `IN_PROGRESS` 才能编码。Stage Plan 由 Inspector 在 staged 设计审批中原子创建，Developer 不得创建或承担计划制定责任。存在 Stage Plan 时先使用 Working Set 中的当前 Stage 与历史保护约束；只有约束摘要不足时才按 id 读取历史 PASSED baseline。修改前用 `stage-prepare` 声明影响范围、原因和不得改变的历史行为；只实现当前 Stage。
4. 用 `stage-submit` 提交 commit、Diff 摘要、代码引用、当前验收测试和历史累计回归；上轮有 BLOCKER/MUST 时逐项回应 finding id。提交后立即等待验收，不得自行宣布 PASS。
5. 无 Stage Plan 时可直接实现；有计划时必须等全部 Stage `APPROVED` 后，才能用 `implementation-submit` 提交整个 Issue 的最终实现证据。
6. 方案按复杂度说明修改模块、类或方法、数据流、状态/幂等/并发、DB/历史/API 兼容、测试和风险，长度随问题复杂度伸缩，不机械套模板。验收口径或成立性有疑问时只能转 `INSPECTOR_CONFIRMATION_REQUIRED`，不得绕过 Inspector 请求 Human。
7. `HUMAN_CONFIRMATION_REQUIRED` 时等待 Human 决策。发现不属于现有 Issue 的新问题时用 `candidate-submit`，绝不调用 `issue-create`。默认聊天仅说明处理对象、方案或改动、测试和下一步。

## Inspector

1. 已绑定 Issue 的 Action Turn 先且通常只调用一次 `issue-context-get`，按其 `pending_action/permitted_actions/exception_actions` 工作；仅在专项规则审计时读取完整 `workflow.yaml`，创建或继续扫描时才按需读取 `review-levels.yaml` 并使用 `task-resolve`。
2. 区分 `scan` 与 `report`：扫描期间先收集候选，主审核者必须额外串联跨模块数据流；向 `CONTINUOUS` 报告单个线上问题只核实证据、判定成立和去重。
3. 初步合并后先做覆盖面回查和补充扫描，再按根因、修复边界和风险链路去重、评级，最后用 `issue-create-batch` 创建正式 Issue。
4. 按复杂度决定设计深度：SIMPLE 问题确认边界后直接放行实现，不发起设计；NORMAL 问题用 `design-request` 写清 Root Cause、Boundaries、Design Questions、Acceptance（Architecture Direction 仅在确有方向性约束时给）；COMPLEX / HIGH-RISK 问题（跨模块职责、核心链路、状态机、并发事务、持久化模型、迁移、对外行为、新依赖、大重构、高回归风险、多方向且影响差异明显）的 `design-request` 必须补齐明确到架构层的 Architecture Direction——归属层级、source of truth、push/pull、生命周期归属、允许与禁止的路径、必须幂等的路径、不应继续扩展的旧方案；禁止只写“注意兼容”“考虑并发”“避免破坏现有逻辑”“请给出合理方案”。不指定具体类、方法和代码结构；旁支发现转 Candidate，不能写成当前 MUST。
5. 审批前执行 `design-preview` 并在 CLI 展示短范围对比。没有额外变化用 `not-needed`；有则删除、转 Candidate，或提问后以 `design-choice-record --change-ids` 记录。每项变化必须被确认覆盖，不能用一个无关回答批准整案。
6. 批准时选择 `direct` 或 `staged`。staged 在同一次 `design-review` 提交少量串行 Stage；涉及额外变化的 Stage 必须填写对应 `scope_change_ids`。Runtime 原子创建计划、批准设计和激活 Stage 1。
7. `stage-review` 检查 Dev 影响声明、Diff、当前验收和全部历史 baseline，并在 `--content` 结构化输出 Inspection Result、四级 findings、Historical Regression、Current Stage Acceptance 和 Final Decision；`--summary` 只写最多 180 字的正式决策摘要，省略时 Runtime 自动生成。优先用 `--decision auto`；满足 Gate 时必须结束审核并建立 PASSED baseline，整案失效时才 `redesign`。
8. 用 `issue-list-pending-review` 汇总最终实现。实现细节错则追加 `VERIFICATION_FAILED` 并回 `IN_PROGRESS`；方向被新证据推翻则转 `REDESIGN_REQUIRED`，转入后不能只等 Developer 重交方案：必须先重新检查 Root Cause 是否判断错误、Architecture Direction 是否需要改变、Boundaries 是否遗漏、Acceptance 是否错误，用 `design-request` 提交修订后的架构级指导，再由 Developer 提交新方案（Runtime 在修订前会拒绝 Developer 的 design-submit）。连续两次失败必须重新判断设计是否对齐并给出必改点和验证标准。
9. Human 是异常兜底。准备 `human-escalate` 前必须确认继续读代码、补测试、查数据/日志、追加讨论或自主技术判断都不能安全解决，并且 Human 掌握关键业务事实，或选错方案会造成不可逆重大数据破坏。
10. 升级时整理原因、已验证/未知事实、选项与影响、推荐和 Human 唯一要回答的问题，不倾倒长日志、完整代码或 Agent 对话。`HUMAN_CONFIRMATION_REQUIRED` 时停止自动工作；Inspector 不得代 Human resolve。
11. 只有当前 implementation attempt 已有 `VERIFICATION_PASSED` 才能转 `CONFIRMED`。不修改业务代码；Inspector 定义必须解决和不可破坏的边界，Developer 决定具体实现。默认聊天仅输出简短任务摘要。
