# Core Workflow

本文件是所有 Code Inspector 身份共享的规范来源。安装后生成的 `SKILL.md` 只负责身份、权限和工具路由，不复制本文件内容。

## 协作记录与可读性

面向 Human 的问题摘要、提交摘要、有效结论和人工决策说明，必须让未参与实现的初中级开发也能直接读懂。先说明解决什么、做了什么、影响是什么、验证结果与下一步；解释必要术语，避免只写内部标识、文件清单或实现流水。允许用独立 Markdown 段落和列表组织，长度以能快速读完为准。完整实现细节、代码推导与角色间争论放在 AI 讨论正文，不要求 Human 通读。

短记录可直接用同一份清晰正文作为摘要；长技术正文才另写 `--human-summary`（最多600字、12行）。正文变更时同步复核摘要。不得截断长正文冒充人话说明。

讨论可指定已有话题；省略时命令复用同类话题或原子创建话题，只有需要独立事项时手动新建。正式动作自动生成 Activity、有效结论和话题关联，不要求模型重复登记 decision；审计记录继续保留。
Web 协作记录：“全部”显示人类摘要，存量无摘要时兼容原展示；“讨论”显示话题分类、标题与完整正文；“处理历史”只显示动作和字段变化；“有效结论”只显示当前生效决定。关键证据放在“技术补充（需要时再看）”折叠区。

操作人和角色是两项独立信息：例如 `codex-insp` 是操作人，`inspector` 是角色。必须使用安装后 SKILL.md 中本平台、本角色的固定工具绝对路径，不把角色名写成操作人，不自行猜测或切换平台身份。角色通用旧入口仅用于兼容，不能代替平台固定身份入口。

## Task 与状态安全

Task 分为一次性治理目标 `REVIEW` 和跨基线长期治理主题 `CONTINUOUS`。`scan` 在两类 Task 中都必须完成跨模块数据流、coverage closure、补扫和去重；向 `CONTINUOUS` 报告单个线上问题的 `report` 只核实证据、判定成立和去重，不触发全项目扫描。默认聊天只输出简短摘要，完整报告仅在用户明确要求时导出。

Task 与 Issue 状态由 Inspector/Developer 按标准状态机维护；Human 可通过 WebApp 人工入口的 `task status` / `issue status` 设置任一合法状态，包括人工确认状态、直接确认和重开终态。Task 只有显式转为 `CLOSED` 时才同步结束 Issue：尚未终结的 Issue 原子转为 `CANCELLED`，已 `CONFIRMED/CANCELLED` 的 Issue 保持不动；其他 Task 状态变更不传播到 Issue。

Inspector/Developer 仍遵守状态前置条件：Agent 进入 `HUMAN_CONFIRMATION_REQUIRED` 使用 `cictl-insp human ask`；Agent 转 `CONFIRMED` 前须为当前 implementation attempt 留有 `VERIFICATION_PASSED`。Human 的通用状态命令不受这些前置条件限制，但仍记录状态变更与审计。

## 方案、派工与 Stage

默认闭环：Inspector 分析并完成必要讨论 → `cictl-insp design dispatch ISSUE @plan.json` 发布方案并派工 → Developer 实现与验证，`cictl-dev impl submit ISSUE --authorization-id ID ...` → `cictl-insp impl review ISSUE @review.json` 验收并收口。

Inspector 负责原始需求、原因、修改边界、业务行为、状态与数据归属、并发事务及失败恢复等方向性选择，并明确验收标准；必要时指出具体模块或代码位置，不写逐行施工说明。已有授权内的技术判断自主决定，只有业务取舍、范围变化及风险授权需要 Human 决定。Developer 负责实现、调查和验证，对命名与局部代码组织自主判断，不能擅自改变方案方向。

方案包含 `content`（原因与处理方式）、`boundaries`、`acceptance` 和独立选择的 `execution_mode: direct|staged`。简单问题几句话即可；不能仅凭文件数、数据库或状态机关键词判断复杂度。复杂方案也可 direct。只有中间成果确有独立验收价值且降低后续风险时才提供 stages。

无需人工选择时一次 dispatch 即可开工。需确认的变化先用 `design request ISSUE @plan.json` 保存 Inspector 草稿，再用已有 `design preview/confirm` 对当前草稿逐项确认；dispatch 只提交 `draft_id` 和 `confirmation_ids`，不重抄方案。新草稿或方案修订使旧授权/不再匹配的确认失效。普通 CLI 确认由 Inspector 据真实回答登记，操作人仍是 Inspector，不冒充 Human。旧 `design submit/approve` 仅保留必要的历史协作入口，不作为新任务默认流程。

context 返回有效 `authorization.activity_id`。Developer 推进实现时须传 `--authorization-id`；状态、批量状态、Stage、正式提交和最终确认均检查有效授权。仅有 IN_PROGRESS 状态不是许可。授权来自不可修改的正式批准 Activity，不来自调用方 metadata。命令约束不是文件系统硬隔离。

Developer 发现前提不成立时暂停受影响实现，使用讨论或 `INSPECTOR_CONFIRMATION_REQUIRED` 说明原前提、代码/数据/协议/测试证据、已知影响及未知项，不要求完整替代方案。Inspector 对普通细节澄清并直接恢复；`issue status ... IN_PROGRESS --authorization-id ID --content ...` 原子绑定本次问询、记录答复和恢复。方向改变则由 Inspector 修订并 dispatch；旧方案和 Stage Plan 留历史但不能继续授权。

新派工的 Stage 继承方案边界和既有 baseline，不再强制额外 stage prepare 或伪造 Developer 声明。需要补充局部影响时仍可 prepare；旧计划沿用原保护要求。Stage submit/review 都引用当前授权。通过后自动激活下一 Stage，整案失效则废弃旧计划。全部 Stage 通过后保留一次轻量最终交付与整体验收，不强制自动关闭。

`impl review` 绑定 `submission_id`、`submission_revision` 和 `authorization_id`，检查原始需求 requirements 与验收项 checks（AC-1..AC-n）。一次写入验证记录、有效结论和状态：pass → CONFIRMED；fix → IN_PROGRESS；replan → REDESIGN_REQUIRED 并撤销旧授权。实现方向错误由 Inspector 修正，不能要求 Developer 反复重提方案。实现正文修订后旧验收版本不能使用。

最终交付可引用通过的 Stage/Activity 与测试记录。`evidence_refs` 指明活动、修订、适用理由，核对代码、依赖和环境 snapshot；与本轮不同则说明 differences 的具体不受影响依据，没有原快照的旧证据补充 source_conditions。无法证明适用就补验。Runtime 检查身份、版本及明确冲突，Inspector 对语义覆盖负责；不建设通用证据中心或自动测试依赖分析。

### 最小充分设计

双方默认使用满足当前需求和已知约束的最直接实现。新增抽象、持久化、任务、依赖、重试、缓存、兼容或降级必须对应具体需求或有依据的失败场景；协议重试、不可信外部输入和实际并发可以构成依据，不必等待事故。不得为假想未来无限加固，不得吞异常、返回默认值或自动降级掩盖错误。

保持行为与边界不变、减少代码和概念的局部巧思由 Developer 自主处理。新增机制或方向变化先简短说明最直接方案、收益、维护成本与失败路径，由 Inspector 判断，必要时询问 Human。Inspector 自己的方案同样受此约束；不增加专门审批表或每任务长清单。

## Review 收敛

Inspector 的 Stage finding 只分四级：`BLOCKER` 是功能、数据、安全、运行、核心语义或历史 Stage 破坏；`MUST` 是明确违反需求、批准设计、验收标准或强制规范；两者阻断。`SHOULD` 是可维护性、抽象、潜在性能或非必要重构，`NIT` 是命名、格式与个人偏好；二者只进 Backlog，绝不阻断。

第一轮可提出所有等级；第二轮起不得新增 SHOULD/NIT，除非由本轮修复新引入并给出证据。第二轮起新增 BLOCKER/MUST 必须说明为何此前未发现、证据、实际风险和阻断依据。

当 `BLOCKER=0`、`MUST=0`、当前 Stage 验收全部 PASS、历史 Stage 累计行为有适用证据且全部 PASS 时，Inspector 必须输出 `PASS` 并结束审核，不能以“还可优化”“最好重构”“不够优雅”继续循环。`cictl-insp stage review` 的 content 与 summary 由命令从结构化审核结果确定性生成，固定包含 Inspection Result、四级 findings、Historical Regression、Current Stage Acceptance 和 Final Decision；正式决策摘要最多 180 字。SHOULD/NIT 存入 baseline/Activity Backlog 后继续通过。governance v2 直接使用 `cictl-insp stage review`，由命令从 findings、当前验收和历史回归自动计算最终 Gate；只有整案失效才使用 `cictl-insp stage redesign`。

最终审核也遵循四级 finding 与收敛规则。原始需求未满足时，即使实现完全遵守 Inspector 方案仍不能通过。实现错误用 impl review 的 fix 退回；方案错误用 replan 返回 Inspector。连续失败时应重新核实事实和方案，不能机械循环。
## Human 升级

Human 只作为极低频最终兜底，不是第三个普通 Reviewer。Developer 遇到边界问题只能走 `INSPECTOR_CONFIRMATION_REQUIRED`；只有 Inspector 能在充分读取代码、测试、数据库结构、文档、协议、历史活动和可得运行数据后调用 `cictl-insp human ask`。

允许升级的原因仅限：关键业务事实或外部约束确实无法由 Agent 获得且直接决定实现方向，或继续自主决策存在重大、不可逆的数据损坏风险。Agent 意见不一致、普通架构取舍、方案质量差、实现或测试失败均不是升级理由，应继续使用证据、方案讨论、设计驳回或 `REDESIGN_REQUIRED` 自主解决。

升级内容必须整理为原因、已验证事实、未知事实、选项与影响、推荐选项以及 Human 只需回答的问题，不得倾倒长日志、完整代码或 Agent 对话。每个选项必须是对象并同时给出 `label` 与 `impact`；只给标题的选项会被 Runtime 拒绝。`HUMAN_CONFIRMATION_REQUIRED` 会暂停自动流转；Human 可在 WebApp 用 `human resolve` 原子记录决定并恢复流程，也可通过通用状态命令直接指定任一合法状态。Agent 恢复后仍负责其设计、实现和验证工作。

异常升级中的 Human 通常没有全程跟进某个 Issue：**升级后必须立刻运行 `cictl-insp human brief <issue_key>` 并把输出原样贴进对话**，让 Human 在 CLI 就能读到选项与影响、未知事实和推荐；不要让 Human 反过来追问细节。Human 的决定仍然只在 WebApp 的「最终边界 / 安全确认」提交。

## 内容、讨论与历史

所有写入 Review DB、最终聊天回复和报告中的人类可读文案默认使用简明中文，目标读者包括测试人员、项目负责人和非当前模块开发者。先说明“发生了什么、影响谁、当前结论、验证结果、下一步或是否可发布”，再按需补充文件、类、接口、状态码等技术依据。避免只堆英文缩写、内部类名、调用链、原始异常、SQL、Diff 或大段代码；无法避免的专业词第一次出现时用一句中文解释。原始状态码、命令、标识符和证据不得篡改，可放在中文说明之后或结构化字段中。

AI 讨论和完整设计可以保留理解问题所需的技术细节。独立讨论结论使用最多180字、3行的日常中文；聚合动作的短记录可复用正文，长记录另给摘要，只写“决定了什么、主要影响、下一步”，不得包含代码块；推理过程和技术证据留在来源讨论或结构化字段。处理历史只用于说明发生过什么操作，Human 页面不重复展开 Agent 的完整工作底稿。

兼容旧设计流程时，`cictl-dev design submit --summary` 必须另写 1 至 3 句方案概要，说明“改哪里、大概怎么改、有什么影响”，让 Human 不阅读完整方案也能掌握方向；完整类、方法、表结构、并发和测试细节放在 `--content`。方案概要最多 180 字，不用内部术语代替业务说明。

新写的代码注释默认使用中文，说明业务目的、边界、风险或“为什么这样做”，让测试和项目负责人能理解行为影响；不要把代码逐句翻译成注释。若目标仓库已有明确的英文注释规范或公共 API 文档语言要求，则遵循项目规范，但面向 Review DB 的说明仍使用中文业务语言。

Git 提交标题和正文也属于人类可读交付物，默认使用简明中文说明“改了什么、解决什么问题、是否影响既有行为”。仓库使用 Conventional Commits 时可以保留 `feat`、`fix`、`refactor` 和 scope 等固定格式，但冒号后的摘要与正文必须使用中文，例如 `refactor(analytics): 统一淘宝数据源边界`；不要生成 `consolidate source boundary` 这类只有开发者容易理解的英文摘要。文件名、类名、命令和 Issue 编号保持原样。仓库明确强制英文提交信息时才遵循该规范。此语言约束不代表获得了执行 `git add`、`git commit` 或 `git push` 的额外授权。

Issue 默认正文只写 `title + summary + dimension + severity`；确有必要时再补 `expected_outcome`、`technical_note`、`local_terms` 和结构化证据。`summary` 用一段短文或 2–5 个要点直说现象、影响和关键原因，可使用轻量 Markdown；不要粘贴推理过程、长日志、完整代码或重复评级。代码位置优先写文件与类/方法/符号，行号只能作为当时快照的辅助提示。通用技术名词无需解释；只定义本项目、业务或临时创造且可能未对齐的术语。

讨论消息使用 当前角色命令 `discussion add/list/edit`，底层与 Activity 分开保存。页面默认用“全部”按最新优先汇总；“讨论”视图除讨论消息外，还投影待协作审核的 `DESIGN_SUBMITTED`、`REDESIGN_SUBMITTED`、`STAGE_SUBMITTED`、`IMPLEMENTATION_SUBMITTED`。这些正式提交仍只保存为 Activity，并继续出现在处理历史，不得复制落库。

Developer、Inspector 修正自己的讨论时直接 amend 原消息。只有尚未被正式业务动作记录的独立讨论结论，才由 Inspector 用 `cictl-insp decision add` 整理并关联来源讨论；相同 `decision_type + scope_key` 的旧结论仅留审计。

当前角色命令 `activity edit` 只用于尚未被消费的提交文案和补充证据。审核、验证、人工决定等最终结论不能 amend；需变化时创建新的正式结论。页面处理历史保留工作流里程碑和结论。普通 Action Turn 使用 当前角色命令 `issue context` 获取有界 Working Set；discussion、activity 与历史 baseline 正文按 id 延迟读取。只有确需兼容旧字段时才用 当前角色命令 `issue show --view full`。
