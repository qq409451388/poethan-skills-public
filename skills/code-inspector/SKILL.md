---
name: code-inspector
description: 在用户明确开启代码检查模式后，按安装时分配的 developer 或 inspector 逻辑身份处理代码检查任务、问题、整改、验证和确认。
---

# Code Inspector

仅在用户明确开启后执行本流程；启动命令与角色选择规则见 `references/activation.yaml`。

安装后的 Skill 会生成当前平台可用的逻辑身份、角色能力、会话角色选择器和固定工具入口。会话角色在启动时确定，并保持到退出；不得根据后续任务自动切换身份。一个实际 Agent 可以承担多个角色，但每个角色必须使用不同 alias；同一角色有多个 alias 时，安装配置必须指定唯一默认身份。只使用当前会话角色对应身份的入口，不直接访问 SQLite 或执行 SQL。

执行前按场景读取本 Skill 内的参考文件：

- 审核、整改、状态变更：`references/workflow.yaml`
- 调用数据库工具：`references/tool-contracts.yaml`
- 选择审核等级：`references/review-levels.yaml`
- 导出报告：`references/report-schema.yaml`

通用规则：Task 分为一次性治理目标 `REVIEW` 和跨基线长期治理主题 `CONTINUOUS`。`scan` 在两类 Task 中都必须完成跨模块数据流、coverage closure、补扫和去重；向 `CONTINUOUS` 报告单个线上问题的 `report` 只核实证据、判定成立和去重，不触发全项目扫描。默认聊天只输出简短摘要，完整报告仅在用户明确要求时导出。

Task 与普通 Issue 状态由 Inspector/Developer 按标准状态机维护；Human 具有最高管理解释权，可通过 `task-update-status` 或 `issue-update-status` 设置任一合法普通状态，包括纠正、重开终态或直接指定待实现审核状态。覆盖操作仍写入 `STATUS_CHANGED` 和审计。Task 只有显式转为 `CLOSED` 时才同步结束 Issue：尚未终结的 Issue 原子转为 `CANCELLED`，已 `CONFIRMED/CANCELLED` 的 Issue 保持不动；其他 Task 状态变更不传播到 Issue。这样结束任务范围但不伪造技术验证结论。两个不能绕过的安全边界是：`HUMAN_CONFIRMATION_REQUIRED` 只能由 Inspector 的 `human-escalate` 进入并由 Human 的 `human-confirmation-resolve` 离开；任何角色转 `CONFIRMED` 前都必须为当前 implementation attempt 留有 `VERIFICATION_PASSED`。

Code Inspector 的目标不是让 Developer 无限提交、Inspector 无限驳回。简单问题可直接实现；跨模块、Schema/迁移、历史回灌、状态机、幂等并发、ACK/retry/recovery、公共 API、大重构或方向不确定的问题，Inspector 应在编码前使用 `design-request`，主动写清根因、不可破坏语义、设计约束、风险、推荐方向和必须回答的问题。Inspector 决定“必须解决什么、不能破坏什么、推荐往哪里做”，Developer 决定“具体代码怎么实现”。

`DESIGN_REQUIRED`、`DESIGN_PENDING_REVIEW`、`REDESIGN_REQUIRED` 期间 Developer 只能阅读、分析、讨论和用 `design-submit` 提交方案，禁止修改业务代码或 `implementation-submit`；只有 `design-review approved` 转为 `IN_PROGRESS` 后才能编码。设计批准只表示基于当时证据允许实现，出现新事实时仍可显式转 `REDESIGN_REQUIRED`。

复杂 Issue 在批准设计前，Inspector 应使用 `stage-plan-create` 把实现拆成少量可独立验收、默认串行的 Stage；每个 Stage 必须明确目标和验收标准。设计批准会激活第一个 Stage。Developer 修改代码前必须读取 `stage-get` 返回的历史 baseline，并用 `stage-prepare` 声明预计修改的模块/文件/类、修改原因以及不得改变的历史行为。之后只实现当前 `IN_PROGRESS` Stage，用 `stage-submit` 提交 commit、Diff 摘要、当前测试、历史累计回归和代码证据；提交后停止修改，等待 Inspector 验收。通过后自动激活下一 Stage，驳回只退回当前 Stage；若验收发现整个设计不成立，使用 `stage-review --decision redesign` 进入 `REDESIGN_REQUIRED` 并废弃旧计划的未完成阶段。新设计建立新的 `plan_no`，旧计划和验收活动永久保留。

每个 `APPROVED` Stage 都必须建立 `PASSED` baseline，记录已验证行为、输入输出契约、重要业务语义和测试集合；后续 Stage 继承之前全部 baseline。Inspector 每轮必须执行 Historical Stage Regression Check，逐项确认当前 Diff、历史行为、历史测试和 Breaking Change。历史 Stage 失败一律是 `BLOCKER`。有 active Stage Plan 时，全部 Stage `APPROVED` 前禁止 `implementation-submit`；全部通过后仍必须提交一次整体验证所需的最终实现，阶段验收不能替代最终 `VERIFICATION_PASSED`。Stage 不属于 Issue 状态机，执行期间 Issue 保持 `IN_PROGRESS`。简单 Issue 不创建 Stage Plan，不增加额外流程。Stage 规划、提交和审核不改变 `current_attempt_no`。

Inspector 的 Stage finding 只分四级：`BLOCKER` 是功能、数据、安全、运行、核心语义或历史 Stage 破坏；`MUST` 是明确违反需求、批准设计、验收标准或强制规范；两者阻断。`SHOULD` 是可维护性、抽象、潜在性能或非必要重构，`NIT` 是命名、格式与个人偏好；二者只进 Backlog，绝不阻断。第一轮可提出所有等级；第二轮起不得新增 SHOULD/NIT，除非由本轮修复新引入并给出证据。第二轮起新增 BLOCKER/MUST 必须说明为何此前未发现、证据、实际风险和阻断依据。

当 `BLOCKER=0`、`MUST=0`、当前 Stage 验收全部 PASS、历史 Stage 累计回归全部 PASS 时，Inspector 必须输出 `PASS` 并结束审核，不能以“还可优化”“最好重构”“不够优雅”继续循环。输出固定包含 Inspection Result、四级 findings、Historical Regression、Current Stage Acceptance 和 Final Decision；SHOULD/NIT 存入 baseline/Activity Backlog 后继续通过。governance v2 优先使用 `stage-review --decision auto` 让 Runtime 计算最终 Gate；只有整案失效才显式使用 `redesign`。

实现审核失败必须区分两类：方案正确但实现遗漏或有 Bug 时记录 `VERIFICATION_FAILED` 并回 `IN_PROGRESS`；方案方向失效时转 `REDESIGN_REQUIRED`，重新经过设计提交和批准。连续两次实现失败后，Inspector 必须重新判断是实现错误还是设计错误；即便仍属实现错误，也必须给出具体失败原因、必改点和验证标准，禁止机械重复循环。

Human 只作为极低频最终兜底，不是第三个普通 Reviewer。Developer 遇到边界问题只能走 `INSPECTOR_CONFIRMATION_REQUIRED`；只有 Inspector 能在充分读取代码、测试、数据库结构、文档、协议、历史活动和可得运行数据后调用 `human-escalate`。允许升级的原因仅限：关键业务事实/外部约束确实无法由 Agent 获得且直接决定实现方向，或继续自主决策存在重大、不可逆的数据损坏风险。Agent 意见不一致、普通架构取舍、方案质量差、实现或测试失败均不是升级理由，应继续使用证据、方案讨论、设计驳回或 `REDESIGN_REQUIRED` 自主解决。

升级前 Inspector 必须确认：继续读代码、补测试、查数据或日志、追加设计指导、作出合理技术判断都不能安全解决；Human 确实掌握 Agent 无法获得的事实，或选错方案确会造成无法靠正常重试恢复的重大破坏。升级内容必须整理为原因、已验证事实、未知事实、选项与影响、推荐选项以及 Human 只需回答的问题，不得直接倾倒长日志、完整代码或 Agent 对话。`HUMAN_CONFIRMATION_REQUIRED` 会暂停 Agent 自动流转；Human 只能用 `human-confirmation-resolve` 给出边界/风险决定并恢复到设计、实现、暂停、受阻或取消，不能直接 `CONFIRMED`。恢复后 Inspector 仍负责设计审核、实现审核、验证和最终技术闭环。

Issue 默认正文只写 `title + summary + dimension + severity`；确有必要时再补 `expected_outcome`、`technical_note`、`local_terms` 和结构化证据。`summary` 用一段短文或 2–5 个要点直说现象、影响和关键原因，可使用轻量 Markdown；不要粘贴推理过程、长日志、完整代码或重复评级。代码位置优先写文件与类/方法/符号，行号只能作为当时快照的辅助提示，不能成为唯一定位依据。通用技术名词无需解释；只对本项目、业务或 Agent 临时定义且可能未对齐的词，在 `local_terms` 中给一句白话定义。

讨论使用 `discussion-append/list/amend`，与处理历史分开。Developer、Inspector 发现自己的讨论消息不准确时，直接 `discussion-amend` 原消息，不追加“更正”或“以此为准”；旧版进入独立 revision。讨论达成一致后，由 Inspector 用 `decision-record` 整理一条短结论并关联来源讨论。后续新结论以相同 `decision_type + scope_key` 覆盖为当前有效结论，旧结论仅留审计。

`activity-amend` 只用于尚未被消费的提交文案和补充证据，例如待审核的设计、Stage 或实现提交。审核、验证、人工决定等最终结论不能 amend；需变化时创建新的正式结论。页面处理历史保留工作流里程碑和结论，默认读取只返回精简 Issue 字段，只有确需兼容旧字段时才用 `issue-get --view full`。
