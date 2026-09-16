# Core Workflow

本文件是所有 Code Inspector 身份共享的规范来源。安装后生成的 `SKILL.md` 只负责身份、权限和工具路由，不复制本文件内容。

## Task 与状态安全

Task 分为一次性治理目标 `REVIEW` 和跨基线长期治理主题 `CONTINUOUS`。`scan` 在两类 Task 中都必须完成跨模块数据流、coverage closure、补扫和去重；向 `CONTINUOUS` 报告单个线上问题的 `report` 只核实证据、判定成立和去重，不触发全项目扫描。默认聊天只输出简短摘要，完整报告仅在用户明确要求时导出。

Task 与普通 Issue 状态由 Inspector/Developer 按标准状态机维护；Human 具有最高管理解释权，可通过 `task-update-status` 或 `issue-update-status` 设置任一合法普通状态，包括纠正、重开终态或直接指定待实现审核状态。Task 只有显式转为 `CLOSED` 时才同步结束 Issue：尚未终结的 Issue 原子转为 `CANCELLED`，已 `CONFIRMED/CANCELLED` 的 Issue 保持不动；其他 Task 状态变更不传播到 Issue。

两个不能绕过的安全边界是：`HUMAN_CONFIRMATION_REQUIRED` 只能由 Inspector 的 `human-escalate` 进入并由 Human 的 `human-confirmation-resolve` 离开；任何角色转 `CONFIRMED` 前都必须为当前 implementation attempt 留有 `VERIFICATION_PASSED`。

## 设计与 Stage

Code Inspector 的目标不是让 Developer 无限提交、Inspector 无限驳回。设计职责按层划分：Inspector 是能力更强的模型，负责 What / Why / Boundary / Architecture Direction / Acceptance——问题为什么发生（Root Cause）、应在哪一层解决和推荐或禁止哪些架构方向（Architecture Direction）、哪些业务语义和兼容性不可破坏、方案必须回答哪些问题（Design Questions）、什么条件满足才算解决（Acceptance）；Developer 负责具体 How——修改哪些模块、类、方法，代码如何组织，数据流、API、DB、状态如何落地，幂等并发事务怎么实现，测试怎么写，Diff 和 Commit 怎么提交。Inspector 不下沉到具体类、方法和代码实现，不把 Developer 变成纯打字工具；Developer 也不重新决定 Inspector 已确定的架构级方向。Developer 发现 Architecture Direction 有事实错误或无法实现时，必须通过讨论或 `INSPECTOR_CONFIRMATION_REQUIRED` 反馈并由 Inspector 修订方向，不得静默改用另一套架构。

设计深度随复杂度自适应，分级规则见 `workflow.yaml` 的 `design_depth`：SIMPLE 问题（局部明确、唯一合理修复、不改外部行为、低风险）由 Inspector 确认问题和边界后直接 `PROPOSED -> IN_PROGRESS`，不要求 design-request 和 Stage Plan；NORMAL 问题（需要一定设计判断，但无架构级调整）的 design-request 至少覆盖 Root Cause、Boundaries、Design Questions、Acceptance，Architecture Direction 仅在确有方向性约束时提供；COMPLEX / HIGH-RISK 问题（跨模块职责调整、runtime/scheduler/supervisor/event 核心链路、状态机、并发事务一致性、持久化模型、数据迁移、对外行为变化、新基础设施、大范围重构、高回归风险、多实现方向且影响差异明显）必须走完整设计流程，Architecture Direction 为必填语义，且要明确到架构层——状态归属的领域对象、source of truth、push 还是 pull、生命周期由谁管理、是否允许跨层访问、在哪一层解决而不是打补丁、哪些路径必须幂等、哪种旧方案不应继续扩展；禁止只写“注意兼容”“考虑并发”“避免破坏现有逻辑”“请给出合理方案”。内容长度随复杂度伸缩，不允许让简单问题也输出五段式长设计文档。

跨模块、数据库结构或数据迁移、历史数据补处理、状态流转、重复执行与并发、消息确认和失败恢复、公共 API、大重构或方向不确定的问题，Inspector 应在编码前使用 `design-request`；旁支发现转 Candidate，不写成当前 MUST。

`DESIGN_REQUIRED`、`DESIGN_PENDING_REVIEW`、`REDESIGN_REQUIRED` 期间 Developer 只能阅读、分析、讨论和用 `design-submit` 提交方案，禁止修改业务代码或 `implementation-submit`；只有 `design-review approved` 转为 `IN_PROGRESS` 后才能编码。设计批准只表示基于当时证据允许实现，出现新事实时仍可显式转 `REDESIGN_REQUIRED`。

批准设计时，Inspector 必须在同一次 `design-review` 中明确选择执行模式：简单问题使用 `direct`；复杂 Issue 使用 `staged` 并提交少量可独立验收、默认串行的 Stage。`staged` 审批会在一个事务中创建 Stage Plan、批准设计并激活第一个 Stage；任何一步失败都整体回滚。每个 Stage 必须明确目标和验收标准。不再提供独立的计划创建命令，Developer 也不承担 Stage Plan 制定责任。

`design-review` 必须绑定当前 Issue 最新的 `DESIGN_SUBMITTED` activity，禁止审核旧设计或其他 Issue 的设计。审批时 Inspector 检查：Developer 是否遵守 Architecture Direction、是否解决 Root Cause 而不是局部打补丁、是否突破 Boundaries、是否回答全部 Design Questions、Acceptance 是否可验证、是否出现无必要复杂化、是否引入 scope change。Inspector 可以在审核中修订自己的 Architecture Direction 并记录原因；但不能因为 Developer 的具体实现方式和自己偏好不同就要求重做——满足方向、边界、验收且风险可控时必须放行。只有原子审批成功、Issue 进入 `IN_PROGRESS` 后，Developer 才开始实现；staged 模式先执行 `stage-get -> stage-prepare -> 实现 -> stage-submit`，direct 模式走无 Stage Plan 的直接实现路径。

Inspector 可以收紧实现约束，但不能把旁支问题变成当前需求。Developer 必须用 `--scope-changes` 列出范围扩大或新增持久化、迁移、外部行为等需确认变化；Inspector 审批前用 `design-preview` 在 CLI 展示“原目标、方案概要、额外变化”。这些变化只能删除、转 Candidate，或经 `design-choice-record --change-ids` 确认；staged 计划中的 `scope_change_ids` 必须逐项对应。Runtime 拒绝未覆盖确认的变化，设计修订后旧确认失效。

CLI 只用日常中文说明变化、影响、选项和推荐，一次最多三个相关决定。普通方法拆分、局部命名等实现细节不询问用户；Issue/Web 仅留档，不要求用户通读。

通过后自动激活下一 Stage，驳回只退回当前 Stage；若验收发现整个设计不成立，使用 `stage-review --decision redesign` 进入 `REDESIGN_REQUIRED` 并废弃旧计划的未完成阶段。新设计建立新的 `plan_no`，旧计划和验收活动永久保留。

每个 `APPROVED` Stage 都必须建立 `PASSED` baseline，记录已验证行为、输入输出契约、重要业务语义和测试集合；后续 Stage 继承之前全部 baseline。Inspector 每轮必须执行 Historical Stage Regression Check，逐项确认当前 Diff、历史行为、历史测试和 Breaking Change。历史 Stage 失败一律是 `BLOCKER`。有 active Stage Plan 时，全部 Stage `APPROVED` 前禁止 `implementation-submit`；全部通过后仍必须提交一次整体验证所需的最终实现。Stage 不属于 Issue 状态机，执行期间 Issue 保持 `IN_PROGRESS`；简单 Issue 不创建 Stage Plan。Stage 规划、提交和审核不改变 `current_attempt_no`。

## Review 收敛

Inspector 的 Stage finding 只分四级：`BLOCKER` 是功能、数据、安全、运行、核心语义或历史 Stage 破坏；`MUST` 是明确违反需求、批准设计、验收标准或强制规范；两者阻断。`SHOULD` 是可维护性、抽象、潜在性能或非必要重构，`NIT` 是命名、格式与个人偏好；二者只进 Backlog，绝不阻断。

第一轮可提出所有等级；第二轮起不得新增 SHOULD/NIT，除非由本轮修复新引入并给出证据。第二轮起新增 BLOCKER/MUST 必须说明为何此前未发现、证据、实际风险和阻断依据。

当 `BLOCKER=0`、`MUST=0`、当前 Stage 验收全部 PASS、历史 Stage 累计回归全部 PASS 时，Inspector 必须输出 `PASS` 并结束审核，不能以“还可优化”“最好重构”“不够优雅”继续循环。`stage-review --content` 固定包含 Inspection Result、四级 findings、Historical Regression、Current Stage Acceptance 和 Final Decision；`--summary` 只保存最多 180 字的正式决策摘要，可省略并由 Runtime 从完整 content 生成。SHOULD/NIT 存入 baseline/Activity Backlog 后继续通过。governance v2 优先使用 `stage-review --decision auto` 让 Runtime 计算最终 Gate；只有整案失效才显式使用 `redesign`。

实现审核失败必须区分两类：方案正确但实现遗漏或有 Bug 时记录 `VERIFICATION_FAILED` 并回 `IN_PROGRESS`，Developer 继续按原设计修复；方案方向被新代码证据、测试或实际约束推翻时转 `REDESIGN_REQUIRED`。进入 `REDESIGN_REQUIRED` 后 Runtime 会先唤醒 Inspector 并阻止 Developer 直接重交方案：Inspector 必须重新检查 Root Cause 是否判断错误、Architecture Direction 是否需要改变、Boundaries 是否遗漏、Acceptance 是否错误，先用 `design-request` 提交修订后的架构级指导，Developer 才能提交新的具体方案。Human 仍可用 `issue-update-status` 人工纠正。连续两次实现失败后，Inspector 必须重新判断是实现错误还是设计错误；即便仍属实现错误，也必须给出具体失败原因、必改点和验证标准，禁止机械重复循环。

## Human 升级

Human 只作为极低频最终兜底，不是第三个普通 Reviewer。Developer 遇到边界问题只能走 `INSPECTOR_CONFIRMATION_REQUIRED`；只有 Inspector 能在充分读取代码、测试、数据库结构、文档、协议、历史活动和可得运行数据后调用 `human-escalate`。

允许升级的原因仅限：关键业务事实或外部约束确实无法由 Agent 获得且直接决定实现方向，或继续自主决策存在重大、不可逆的数据损坏风险。Agent 意见不一致、普通架构取舍、方案质量差、实现或测试失败均不是升级理由，应继续使用证据、方案讨论、设计驳回或 `REDESIGN_REQUIRED` 自主解决。

升级内容必须整理为原因、已验证事实、未知事实、选项与影响、推荐选项以及 Human 只需回答的问题，不得倾倒长日志、完整代码或 Agent 对话。`HUMAN_CONFIRMATION_REQUIRED` 会暂停自动流转；Human 只能用 `human-confirmation-resolve` 给出边界/风险决定并恢复流程，不能直接 `CONFIRMED`。恢复后 Inspector 仍负责设计审核、实现审核、验证和最终技术闭环。

## 内容、讨论与历史

所有写入 Review DB、最终聊天回复和报告中的人类可读文案默认使用简明中文，目标读者包括测试人员、项目负责人和非当前模块开发者。先说明“发生了什么、影响谁、当前结论、验证结果、下一步或是否可发布”，再按需补充文件、类、接口、状态码等技术依据。避免只堆英文缩写、内部类名、调用链、原始异常、SQL、Diff 或大段代码；无法避免的专业词第一次出现时用一句中文解释。原始状态码、命令、标识符和证据不得篡改，可放在中文说明之后或结构化字段中。

AI 讨论和完整设计可以保留理解问题所需的技术细节。正式决策结论必须压缩为最多 180 字、最多 3 行的日常中文，只写“决定了什么、主要影响、下一步”，不得包含代码块；推理过程和技术证据留在来源讨论或结构化字段。处理历史只用于说明发生过什么操作，Human 页面不重复展开 Agent 的完整工作底稿。

存在设计时，`design-submit --summary` 必须另写 1 至 3 句方案概要，说明“改哪里、大概怎么改、有什么影响”，让 Human 不阅读完整方案也能掌握方向；完整类、方法、表结构、并发和测试细节放在 `--content`。方案概要最多 180 字，不用内部术语代替业务说明。

新写的代码注释默认使用中文，说明业务目的、边界、风险或“为什么这样做”，让测试和项目负责人能理解行为影响；不要把代码逐句翻译成注释。若目标仓库已有明确的英文注释规范或公共 API 文档语言要求，则遵循项目规范，但面向 Review DB 的说明仍使用中文业务语言。

Git 提交标题和正文也属于人类可读交付物，默认使用简明中文说明“改了什么、解决什么问题、是否影响既有行为”。仓库使用 Conventional Commits 时可以保留 `feat`、`fix`、`refactor` 和 scope 等固定格式，但冒号后的摘要与正文必须使用中文，例如 `refactor(analytics): 统一淘宝数据源边界`；不要生成 `consolidate source boundary` 这类只有开发者容易理解的英文摘要。文件名、类名、命令和 Issue 编号保持原样。仓库明确强制英文提交信息时才遵循该规范。此语言约束不代表获得了执行 `git add`、`git commit` 或 `git push` 的额外授权。

Issue 默认正文只写 `title + summary + dimension + severity`；确有必要时再补 `expected_outcome`、`technical_note`、`local_terms` 和结构化证据。`summary` 用一段短文或 2–5 个要点直说现象、影响和关键原因，可使用轻量 Markdown；不要粘贴推理过程、长日志、完整代码或重复评级。代码位置优先写文件与类/方法/符号，行号只能作为当时快照的辅助提示。通用技术名词无需解释；只定义本项目、业务或临时创造且可能未对齐的术语。

讨论消息使用 `discussion-append/list/amend`，底层与 Activity 分开保存。页面默认用“全部”按最新优先汇总；“讨论”视图除讨论消息外，还投影待协作审核的 `DESIGN_SUBMITTED`、`REDESIGN_SUBMITTED`、`STAGE_SUBMITTED`、`IMPLEMENTATION_SUBMITTED`。这些正式提交仍只保存为 Activity，并继续出现在处理历史，不得复制落库。

Developer、Inspector 修正自己的讨论时直接 amend 原消息。讨论达成一致后，由 Inspector 用 `decision-record` 整理短结论并关联来源讨论；相同 `decision_type + scope_key` 的旧结论仅留审计。

`activity-amend` 只用于尚未被消费的提交文案和补充证据。审核、验证、人工决定等最终结论不能 amend；需变化时创建新的正式结论。页面处理历史保留工作流里程碑和结论。普通 Action Turn 使用 `issue-context-get` 获取有界 Working Set；discussion、activity 与历史 baseline 正文按 id 延迟读取。只有确需兼容旧字段时才用 `issue-get --view full`。
