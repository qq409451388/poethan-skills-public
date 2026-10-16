---

一、现状：已有的防过度设计机制（含强制程度）

┌────────────────────────────────────────────────────────────────────────────────┬─────────────────────────────────┬────────────────────┐
│                                      机制                                      │              位置               │   是否运行时强制   │
├────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼────────────────────┤
│ Stage 验收收敛：二轮起不得新增 SHOULD/NIT（除非                                │                                 │                    │
│ introduced_by_fix=true+证据）；新增 BLOCKER/MUST 需                            │ runtime/cictl.py:994-1012       │ 强制               │
│ why_not_found_earlier+evidence+risk                                            │                                 │                    │
├────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼────────────────────┤
│ Gate 双向锁死：BLOCKER/MUST 未清零不能 PASS；已清零不能 REJECT；redesign       │ runtime/review_db.py:2548-2576  │ 强制               │
│ 必须至少一个 BLOCKER                                                           │                                 │                    │
├────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼────────────────────┤
│ 范围变更逐项确认：confirmation=not-needed 时存在 scope_changes 直接报错；每个  │ runtime/review_db.py:1719-1767  │ 强制               │
│ change id 必须被确认记录覆盖                                                   │                                 │                    │
├────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼────────────────────┤
│ Stage 开发前声明 + 提交证据：v2 必须 stage-prepare 在先，提交需                │                                 │                    │
│ diff/code-reference/test-evidence，且 resolved_findings 必须覆盖上轮全部       │ runtime/review_db.py:2435-2482  │ 强制               │
│ BLOCKER/MUST                                                                   │                                 │                    │
├────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼────────────────────┤
│ Dev 不能把顺带发现变成正式任务：issue-create 不在 developer 命令集，越权报     │ agents/agent-roles.json +       │ 强制               │
│ PERM_COMMAND_FORBIDDEN                                                         │ cictl.py:1546                   │                    │
├────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼────────────────────┤
│ 设计深度分级（SIMPLE 免设计、COMPLEX 才要求架构方向、禁止五段式套模板）        │ references/workflow.yaml:285-31 │ Prompt             │
│                                                                                │ 1                               │                    │
├────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼────────────────────┤
│ 职责分层：Inspector 管 What/Why/Boundary/Acceptance，Dev 管                    │ workflow.yaml:190-213, 267-278  │ Prompt             │
│ How；审批检查项含"是否出现无必要的复杂化"                                      │                                 │                    │
├────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼────────────────────┤
│ difficulty 反抑制：先界定最小必要修复范围、禁止拿"核心链路"加档、判 5          │ references/report-schema.yaml:9 │ Prompt（只影响选执 │
│ 需不可逆证据                                                                   │ 5-116                           │ 行者）             │
├────────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┼────────────────────┤
│ poethan-clean-code：YAGNI 规则最完整（"先证明复杂度必要"、"最佳实践/以后可能扩 │ skills/poethan-clean-code/SKILL │ 与 CodeInspector   │
│ 展/更加通用"单独出现即无效、"修改范围保持局部"）                               │ .md:41-49                       │ 完全未接线         │
└────────────────────────────────────────────────────────────────────────────────┴─────────────────────────────────┴────────────────────┘

结论前置：审核侧（Stage 验收）的防加码机制做得相当扎实且是运行时强制的；过度设计的入口不在那里。

---

二、执行链路：约束实际怎么传递

Inspector 扫描 → issue create-many ──┐
│  required 只有 title/dimension/severity/summary
▼  (cictl.py:320) —— evidence/trigger_conditions 全可选
Issue(PROPOSED)
│  issue_projection._pending_action()
▼  (skills/.../scripts/issue_projection.py:44-45)
Developer 收到 pending_action = triage_or_implement_issue
│  ← 只看 status，不看 disposition/confidence/cost
▼
设计（SIMPLE 可跳过）→ stage-prepare 声明范围 → 编码 → stage-submit
│                        ↑ 声明与 Diff 无任何比对
▼
stage-review（gate 强制、收敛强制）→ SHOULD/NIT 留在 review_findings_json

关键点：约束在链路后端（验收）很强，在前端（建 Issue、定范围、写代码）几乎只剩 Prompt。

---

三、问题定位

已证实的问题

P1（核心）：disposition / confidence / trigger_conditions 是死字段，"问题成立"被直接等同于"排队待实现"。

- disposition 有 8 个值（含 observe/defer/opportunistic_fix，runtime/review_db.py:73-77），但全仓库检索显示它只被写入和校验，不参与任何控制流
  ；issue_projection._pending_action()（issue_projection.py:24-59）完全按 status 推导：PROPOSED → triage_or_implement_issue。
- severity/benefit/cost/confidence 的唯一作用是 issue list 的 ORDER BY（review_db.py:1018-1035）——排序，不是过滤。
- trigger_conditions 在 schema 里是可选数组（cictl.py:342），issue create 的 required
  只有四个描述性字段（cictl.py:320）。没有任何字段能区分"已复现缺陷"与"推测性风险"。
- 状态机里没有"已知、本次不做"的一等状态；唯一能让 pending_action 变 None 的停靠点是
  ON_HOLD（issue_projection.py:10-12），但其语义是"等待"，文档从未指示低频/推测性 Issue 应停在 ON_HOLD。

这就是核心问题的答案：CodeInspector 确实把"问题确实存在"错误地等同于"本次必须解决"，而且这个等号是在 Issue
创建的瞬间、在元数据层就被划上的，与分级机制无关。

P2：Developer 侧没有任何"最小实现"约束，且已有的最相关规则没有接入。

- core-workflow.md / role-workflows.md 的 Developer 章节逐条看下来，约束只有流程性字段（scope_changes 声明、stage 证据、逐项回应 finding
  id），没有一条涉及"不引入无调用方的抽象""不顺带重构""最简单方案优先"。
- 全仓库检索 KISS|YAGNI|最小必要|过度设计|无必要：除 Inspector 侧 difficulty 评级的"最小必要修复范围"外，Developer 侧零命中。
- 唯一成体系的规则在 poethan-clean-code（"先证明复杂度必要"、无效理由清单、改动局部化），但它与 code-inspector 互不引用（双向 grep
  为空），且其 description 限定 Java/Spring——非 Java 项目完全裸奔。

P3：scope_changes 的"必填"只写在文档里，代码里默认空数组。

- workflow.yaml:237 写 developer_required: true；实际 derive_design_submit 是 payload.get("scope_changes", [])（cictl.py:1098-1104），schema
  里 scope_changes 也不在 required。Dev 漏填 = 静默声明"无范围外变化"，直接通过。

P4："Backlog" 是文档虚构的机制，SHOULD/NIT 无处可去——这反过来会推高 MUST 的使用。

- core-workflow.md:41、workflow.yaml:373,400-401 都说 "SHOULD/NIT 存入 baseline/Activity Backlog"；但运行时与迁移里 没有任何 backlog
  结构、命令或视图（grep backlog 仅命中这三处文档）。findings 只留在该 Stage 的 review_findings_json 里。
- 后果是一个真实的激励扭曲：Inspector 若真心认为某改进值得做，写 SHOULD 等于让它永久消失，只有写成 MUST
  才能推动它落地。这与"追求代码质量反而推动更多非必要修改"是同一个机制。

有依据但尚未验证的风险

- R1：MUST 的定义为"明确违反…或强制工程规范"（core-workflow.md:37），"强制工程规范"没有白名单/引用源，给了把主观质量偏好上纲为阻断项的合法空
  间。运行时不校验 MUST 的归类依据（只校验有 evidence/risk 字段）。
- R2：Stage 粒度与数量无上限，use_for 只给了"改动范围大/多模块/…"的正向标准（workflow.yaml:315），没有"不得为拆而拆""SIMPLE 不允许
  staged"的反向约束；每个 Stage 都带 prepare/submit/review 的固定开销。
- R3：stage-prepare 声明的 change_scope 与实际 Diff 从不比对（review_db.py:2435-2490 只校验字段非空），Dev 侧范围漂移只能靠 Inspector 读 diff
  发现。

当前无法确认

- 实际过度设计的案例落在哪个阶段——需要真实 Issue/Stage 数据（例如统计"二轮后仍新增 BLOCKER/MUST 的比例""resolved_findings 中非 finding
  的额外改动"）。机制分析只能定位通道，无法给出发生率。

---

四、四个阶段的风险排序（基于证据）

┌─────────┬──────────┬───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│  阶段   │   风险   │                                                       依据                                                        │
├─────────┼──────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Inspect │ 高（主因 │ 每个"成立"问题自动变成 Dev 待办；L2/L3 维度含 architecture_extensibility（"扩展点与接口稳定性""扩展新类型是否依赖 │
│ or 建   │ ）       │ 持续增加条件分支"，review-levels.yaml:56-60）——这类扫描天然产出"应当抽象"的 Issue；无"是否值得修"过滤             │
│ Issue   │          │                                                                                                                   │
├─────────┼──────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Dev 自  │ 高       │ 见 P2/P3/R3：无最小实现规则、范围声明可静默为空、声明与 Diff 不校验                                               │
│ 主开发  │          │                                                                                                                   │
├─────────┼──────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Inspect │          │                                                                                                                   │
│ or 设计 │ 中       │ design_depth 判级、Stage 划分全部靠 Inspector 自判，无反向约束（R2）；设计内容长度/五段式有约束但只是 Prompt      │
│ 规划    │          │                                                                                                                   │
├─────────┼──────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Inspect │          │ Gate 双向锁死 + 收敛规则 + resolved_findings 强制是运行时强制的；唯一弱点是"连续两次失败必须重新判断设计"是       │
│ or 审核 │ 低       │ Prompt only（workflow.yaml:439-442，runtime_auto_transition: false）                                              │
│ 返工    │          │                                                                                                                   │
└─────────┴──────────┴───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

---

五、改进建议（按必要性；全部是改现有规则/字段，不新增机制）

1. 让"存在但本次不做"进入控制流（改 1 个投影函数）：issue_projection._pending_action() 读 disposition——observe/defer/business_confirmation 的
   Issue 对 Developer 返回 pending_action=None，只在 Inspector 视图保留。这是切断"存在=必修"的唯一直接手段，改动面极小（该函数是纯函数，已有 4 个测试覆盖）。
2. 把推测性发现逼出可验证形态：对 dimension ∈ {architecture_extensibility, code_quality, test_observability} 或 severity ∈ {medium, low} 的 Issue，把 trigger_conditions 或 evidence 提升为 schema required（cictl.py:320 一处改动）。运行时不判断内容真假，但强制 Inspector 写出可验证触发条件，让"以后可能有问题"这类表述在创建时就暴露。
3. 给 SHOULD/NIT 一个真实归宿，或改掉文档：最小做法是允许 stage review 的 SHOULD/NIT 一键转 Candidate（candidate 机制已存在，candidate-submit 只是 Dev 侧命令，需给 Inspector 开放或由 Inspector 手动 issue create 并在同一次审批里显式说明）。若不做，就把 core-workflow.md:41 / workflow.yaml:373 的 "Backlog" 措辞改成实情——留着一个不存在的机制比没有更糟，因为它制造了"写 SHOULD 等于丢弃"的激励。
4. 给 Dev 补最小实现约束（Prompt 级，不新增机制）：在 core-workflow.md / role-workflows.md 的 Developer 章节直接引入 poethan-clean-code 的核心判据（"默认最简单实现""最佳实践/以后可能扩展/更加通用"单独出现即无效），并在 design submit 正文中要求写出"最简单方案是什么、为什么不够"（复用现有 --content，不加字段）。同时决定是否把 clean-code 的适用范围从 Java 扩到通用，或在 code-inspector 里显式引用它。
5. scope_changes 从默认空改为必填：cictl.py:317-322 把 scope_changes 加入 required（或至少在 derive_design_submit 里对缺失报错），强制 Dev 显式声明"无"。一行改动，消除 P3。

不推荐的：为 SHOULD/NIT 新建 backlog 表、为"过度设计"新增 finding 等级、引入评审打分校准模型——这些都属于"为治理过度设计而引入另一套复杂机制"。

---

核心问题的正面回答

为什么已有分级和审核机制，Codex 仍会过度设计？

因为现有分级机制治理的是**"一条已知要修的问题该怎么修、验收时不能被无限加码"——它作用在 Stage 验收这一层，而且做得很好。而过度设计的源头在它上游**：

- 建 Issue 阶段：分级（severity/dimension/disposition/confidence）只决定排序和派谁做，不决定做不做；元数据字段（disposition/confidence/trigger_conditions）全部是死字段，"问题成立"在创建瞬间就被翻译成 Dev 的待办动作。
- 编码阶段：唯一能压制"额外抽象/预备实现/顺带重构"的规则集（poethan-clean-code）没有接入 CodeInspector，Dev 侧实际没有任何最小实现约束，范围声明还可以静默为空。
- 结果是：Inspector 只被要求"证明问题存在"，从未被要求"证明这次值得修、且最小修法是这个"；Dev 只被要求"实现所批准的设计"，从未被要求"证明复杂度是必要的"。审核端再严格，也拦不住一个从一开始就过大的任务范围。

最值得调整的一点：把 disposition 从装饰性元数据变成真正参与控制流的字段（建议 1）——让"已经确认存在、但本次不修"成为系统里合法且可表达的状态。这一处改动的收益最大、代价最小，且不需要任何新机制。