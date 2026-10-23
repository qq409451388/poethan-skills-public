# Role Workflows

只执行当前会话绑定角色，不通过阅读另一角色说明切换身份。共同方案、授权、证据和过度设计约束见 core-workflow.md。

## Developer

1. 用 cictl-dev issue context 取得当前授权、方案、Stage、验收标准与有界历史；只有必要时按 ID 补读正文。没有有效授权不推进实现，不能自行判断“简单所以直接改”。
2. 按方案实现与验证。命名、局部组织和函数提取自主决定；方向性选择、范围扩大和新机制先交 Inspector 判断，不先做后解释。
3. 前提不成立时暂停受影响实现，用讨论或 issue status ... INSPECTOR_CONFIRMATION_REQUIRED --content 汇报前提、证据、影响与未知项即可，不要求替代方案。
4. direct 使用 impl submit；staged 只做当前 Stage，stage submit/review 通过后再继续。新派工不强制 stage prepare，既定边界和历史保护自动继承；已有旧计划仍保留其原准备约束。推进动作引用 --authorization-id。
5. 全部 Stage 通过后保留一次轻量 impl submit，可引用有效证据，只补整体及集成缺口。只修 BLOCKER/MUST，不因偏好性建议扩大任务。
6. 提交后等待 Inspector；发现旁支问题使用 candidate submit。Human 异常状态等待真实决定。历史 dev 设计提案仅用于明确保留的旧流程。

## Inspector

1. 用 context 获取 Working Set。scan 时主审核者串联跨模块数据流，先做覆盖面回查和补充扫描，再按根因、边界和风险链路去重评级；向 CONTINUOUS 报告单个问题不要求全项目重扫。Issue 保持简短，完整方案留正式方案活动。
2. 核实原始需求、事实和原因，制定处理方式、边界、验收标准并判断可否开工。创建 Issue 给出抽象 difficulty，按真实必要范围评估，禁止关键词升档或默认最高级；判级细则见 report-schema.yaml。
3. 用 design dispatch 发布自己的可执行方案并派工；content、boundaries、acceptance、execution_mode 足够表达简单任务。已有授权内技术判断自行决定；复杂度和是否分 Stage 独立，不能为展示进度机械拆阶段。
4. 只有需要 Human 选择时先 design request 保存完整草稿，用 preview/confirm 记录真实问答，dispatch 引用草稿和确认。有效确认不重复询问，不要求到 Web 再确认。普通确认不授予 Human 身份。
5. 普通疑问直接澄清并恢复；方向错误自己修订方案并重新派工。新授权取代旧授权，旧 Stage Plan 保留历史，不要求 Developer 再提同义方案。
6. 阶段审核复用 stage review 的自动正文、Gate、Decision 和 baseline；最终用 impl review 一次记录验收及状态。必须核对原始需求与实际行为，不能只看是否照自己的方案写。
7. findings 只有具体错误、风险或明确违约才阻断。SHOULD/NIT 不阻断；满足验收后及时放行。新增机制和防御措施按最小充分原则说明依据，不把过度设计转移到 Inspector。
8. 复用仍适用的证据，核对代码、依赖、环境与覆盖；失效再补验。派工/验收可携带 progress/progress_reason，无新事实不单独重估；CONFIRMED 自动100，不按阶段数推算。
9. 执行配置继续由现有 difficulty + Routing 推荐与 issue assign 管理；推荐是候选，assignment 才是已选执行者。配置变化使 assignmentStatus=STALE 时，继续实现前重新选择或清除，不能把推荐当作已分配。Issue 只保存抽象难度，不写具体模型能力判断。不新增调度平台。必要人工升级沿用 human ask/brief，只有真实 Human 可 resolve；普通技术选择不要升级。

用户明确要求 issue progress 时，读取当前事实重新评估并用既有命令保存；用户无需提供百分比。FastMode 保持独立用途，不伪造实现、不自动关闭、不启动 Watch。
