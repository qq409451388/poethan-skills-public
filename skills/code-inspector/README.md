# Code Inspector

本机代码检查协作 Skill。它通过本地 SQLite 保存任务、问题、活动记录和审计日志；数据库只能经领域命令访问，Agent 不直接执行 SQL。

## 角色分工

默认绑定定义在 [agents/agent-roles.json](agents/agent-roles.json)：

| 机器 Agent | 平台 | Code Inspector 角色 |
| --- | --- | --- |
| `codex-dev` | Codex | `developer`：提交具体方案，获批后修改代码并提交实现 |
| `codex-insp` | Codex | `inspector`：创建任务和问题、提出设计约束、审核方案和实现、最终确认 |
| `trae-inspector` | Trae-CN | `inspector`：创建任务和问题、提出设计约束、审核方案和实现、最终确认 |
| `claude-inspector` | Claude | `inspector`：创建任务和问题、提出设计约束、审核方案和实现、最终确认 |
| `dsh-developer` | DeepSeek Harness | `developer`：提交具体方案，获批后修改代码并提交实现 |
| `dsh-inspector` | DeepSeek Harness | `inspector`：创建任务和问题、提出设计约束、审核方案和实现、最终确认 |

`inspector` 同时承担验证职责，不使用独立 `verifier` Agent。

同一个实际 Agent 可以承担多个角色，但每个角色必须配置不同 alias，以便权限校验和审计日志明确区分逻辑身份。同一平台、同一角色配置多个 alias 时，必须且只能有一个绑定设置 `"default": true`。角色的会话启动参数由 `session_selector` 配置；当前 `dev` 表示 developer，`insp` 表示 inspector。

## Developer 执行模型推荐（可选）

Inspector 只输出抽象的 `difficulty`（1 起正整数，越高要求越高），不感知任何具体模型名称。是否把它换算成“哪些 Dev 执行配置可以完成这个任务”，由本机级配置文件决定：

```text
~/.agent-review/config/agent-routing.yml      # 你的执行配置
~/.agent-review/config/agent-capabilities.yml # Skill 内置的 Agent/Model 能力表
```

该文件不存在时功能完全不启用，Issue 照常创建、状态机照常流转，只是没有推荐列表。配置结构见 [config/agent-routing.example.yml](config/agent-routing.example.yml)：

```yaml
version: 2
profiles:
- id: codex-gpt-5.6-sol-high
  agent: codex
  model: gpt-5.6-sol
  reasoning: high
  level: 3
  enabled: true
```

每条记录都是一个 **Dev 执行配置**：Agent、Model、Reasoning、等级。`role` 不是可配置维度——执行配置只代表 Developer 能力，旧配置里的 `INSPECTOR` 条目在读入时会被丢弃。筛选语义是 `enabled = true` 且 `level >= difficulty`，`level` 取离散值 `1..5`，与 `difficulty` 同一尺度；结果只是候选，不改变 Issue 状态机，也不代表强制调度结果。

### Model 与 Reasoning 的能力来源

可选的 Model 与 Reasoning 档位来自 Skill 内置的 [config/agent-capabilities.yml](config/agent-capabilities.yml)，结构为 `agent -> models[] -> supportedReasonings[]`：

```yaml
agents:
- agent: codex
  models:
  - model: gpt-5.6-sol
    supportedReasonings: [minimal, low, medium, high]
  - model: gpt-5.1-codex-mini
    supportedReasonings: [low, medium, high]
```

同一个 Agent 可以维护多个 Model，每个 Model 有**自己**的 Reasoning 范围；不假设都支持 `high`/`xhigh`。WebApp 的下拉与后端校验读同一份数据，因此不会出现“后端允许但页面无法表达”的档位。未收录的模型不会被拒绝，而是退回通用档位集合。

本机 discovery 只做两件事：发现安装了哪些 Agent、读取其当前默认模型与推理档位作为初始推荐；它不判断模型能力，也不会把同一个 Agent 压成单模型。

### 推荐与分配

- `difficulty` 是 Inspector 输出的抽象能力要求。
- 推荐（`recommendedExecutors`）由 Router 按**当前**配置动态计算，是 `difficulty + 当前 Routing 配置` 的投影，不落库、不缓存。读取 Issue 不会写数据库，也不会推进 `projection_revision`。
- 真正选定的执行者是独立的 `assignment` 字段，用 `issue-set-assignment` 写入。只接受 `enabled` 且 `level >= difficulty` 的合法候选。

两者生命周期不同：Routing 配置变化只会让 assignment 变成 `STALE`，**不会自动改选**，也不会删除历史分配。`reviewctl issue show` / `reviewctl issue context` 返回：

```json
{
  "assignment": {"profileId": "...", "agent": "...", "model": "...", "reasoning": "...", "level": 3},
  "assignmentStatus": "STALE",
  "assignmentInvalidReason": "profile_level_below_difficulty"
}
```

`assignmentStatus` 取 `VALID` / `STALE` / `NONE`。判定只看**当前配置内容**，不看历史 `routingRevision`：即使配置整体重写，只要被选 profile 的 agent/model/reasoning/level 仍然一致且满足 difficulty，就仍是 `VALID`。失效原因包括 `router_disabled`、`profile_missing`、`profile_disabled`、`profile_changed`、`profile_level_below_difficulty`、`profile_level_reduced`。

`projection_revision` 只表示 Issue 核心 Working Set / Workflow 状态变化：`difficulty` 与 `assignment` 的变更会推进它，而 Routing 配置变化、推荐重算、单纯读取 Issue 都不会。

### 页面与保存流程

配置的查看与修改统一在 WebApp 的「模型路由配置」页面完成（`/routing`）：按 Agent 分组、一个 Agent 下配置多个 Model、Model 从 capability 选择、Reasoning 随 Model 联动、等级用离散滑杆。保存流程是「完整校验 → 写临时文件 → 原子替换 → reload 运行时快照」，校验失败不修改现有文件，reload 失败继续沿用上一份有效配置，都不需要重启 WebApp。从本机 Agent 初始化时，已有配置默认**合并**（只追加新组合，保留人工调整过的等级与启用状态），整体替换必须显式选择。

## 安装

macOS / Linux 在仓库根目录执行：

```bash
python3 scripts/code-inspector-installer/install.py install
python3 scripts/code-inspector-installer/install.py verify

# 安装后把唯一的全局命令加入 PATH（一次性）：
echo 'export PATH="$HOME/.agent-review/bin:$PATH"' >> ~/.zshrc
command -v reviewctl   # 应输出 ~/.agent-review/bin/reviewctl
```

Windows 在 PowerShell 中执行：

```powershell
python scripts/code-inspector-installer/install.py install
python scripts/code-inspector-installer/install.py verify
```

支持 macOS、Linux 和 Windows。安装器先把完整内容复制到目标目录旁的临时路径，准备完成后再替换旧版本；失败时恢复旧版本。Agent 目录不使用软链接，仓库中的半成品修改不会立即影响正在使用的 Skill。

安装器会创建：

```text
~/.codex/skills/code-inspector/       # 为 Codex 生成的角色 Skill
~/.trae-cn/skills/code-inspector/     # 为 Trae-CN 生成的角色 Skill
~/.claude/skills/code-inspector/      # 为 Claude 生成的角色 Skill
~/.dsh/skills/code-inspector/         # 为 DeepSeek Harness 生成的角色 Skill

以上目录中的 references/ 复制自：
<repository>/skills/code-inspector/references

~/.agent-review/bin/reviewctl        # 全局唯一 CLI 入口（加入 PATH 后 command -v reviewctl 可用）
~/.agent-review/bin/reviewctl.py     -> 复制自 <repository>/scripts/code-inspector-installer/runtime/reviewctl.py
~/.agent-review/bin/review-db.py     # 内部兼容层，使用说明不再暴露
  -> 复制自 <repository>/scripts/code-inspector-installer/runtime/review_db.py
```

运行数据位于 `~/.agent-review/`，包括数据库、日志、导出内容和安装后的有效配置；这些数据不进入 Git。

### 规则单一来源

安装后生成的 `SKILL.md` 只保存当前平台的角色选择、alias、命令权限、固定工具路径和参考文件路由，不复制公共工作流正文。Agent 规则由 Git 仓库中的以下文件统一维护：

```text
references/core-workflow.md   # 所有角色共享规则
references/role-workflows.md  # Developer / Inspector 分角色流程
references/workflow.yaml      # 状态机
references/tool-contracts.yaml
references/watch-mode.md
references/thread-runtime.md
config/runtime.json
```

修改这些源文件后重新执行安装，安装器会一次性替换 Agent 使用的完整副本，不需要同步修改生成器中的 Skill 文案。

如果目标路径已有旧版普通文件，安装器会拒绝覆盖。确认该目录是旧安装器内容后可使用：

```bash
python3 scripts/code-inspector-installer/install.py --force install
```

## 使用方式：显式开启会话模式

Code Inspector 默认不创建任务，也不写数据库。必须先在当前会话说：

```text
进入代码检查模式
```

或：

```text
$code-inspector start
```

当当前 Agent 只配置一个角色时，`start` 可以不带参数。配置多个角色时必须显式选择：

```text
$code-inspector start dev
$code-inspector start insp
```

`/code-inspector` 形式也兼容。角色在启动时锁定，不会根据后续任务自动切换；需切换时先退出再重新启动。开启后，明确要求“开始审核”“创建审核任务”“处理审核问题”“提交实现”“验证修复”或“最终确认”时，才进入对应工作流。退出模式使用：

```text
退出代码检查模式
```

或：

```text
$code-inspector stop
```

完整触发规则见 [references/activation.yaml](references/activation.yaml)。

## 任务与问题的边界

Task 有两类：`REVIEW` 是一次边界明确的检查治理目标，继续沿用项目、等级、目标、范围和基线 identity；`CONTINUOUS` 是可持续数月的治理主题，identity 不包含 `baseline_ref`，代码基线变化仍复用同一 Task。类型创建后不可修改。`CONTINUOUS` 的全部 Issue 关闭后 Task 仍保持活动，只有 Inspector/Human 显式关闭或取消才结束。Task 显式转为 `CLOSED` 时，尚未终结的 Issue 会在同一事务中转为 `CANCELLED`；已确认或已取消的 Issue 不变。Task 的其他状态变更不会同步 Issue。

Inspector 修改 Task 状态时遵守标准状态机。Human 具有 Task 状态最高管理权限，可纠正状态或重新打开 `CLOSED / CANCELLED` 任务；操作仍通过 `task-update-status` 记录审计，不改变 Issue 级专用流程约束。

同样，Human 可通过 `issue-update-status` 覆盖任一普通 Issue 状态，例如从 `REDESIGN_REQUIRED` 直接指定为 `IMPLEMENTED_PENDING_REVIEW` 或重新打开终态。该操作用于人工纠错和最高解释，不改变 Inspector/Developer 的标准规则。`HUMAN_CONFIRMATION_REQUIRED` 仍是保留异常通道，禁止用通用状态命令进入或离开；最终 `CONFIRMED` 对所有角色都要求当前 implementation attempt 已有 `VERIFICATION_PASSED`。

“继续审核、扫描项目、专项检查”属于 `scan`，无论 Task 类型都必须执行 coverage closure、跨模块回查、补扫和完整去重。“把这个线上 Bug 记入长期任务”属于 `report`，只需核实证据、确认成立、去重并创建 Candidate/Issue，不要求重新扫描整个项目。

扫描时先收集候选问题，完成范围扫描后再统一去重和落库。相同根因、修复边界和风险链路只能是一个 issue，多个位置应作为它的证据。未终态 issue 重复出现时不新建；已经确认的问题再次出现时才创建新的 issue 和版本。

默认聊天输出仅包含 task 编号、本轮新增/重复问题数、最高风险和下一步。只有明确要求导出时才输出完整 Markdown 或 JSON 报告。

正式 Issue 默认只需要标题、短摘要、维度和严重度；完成标准、技术补充、本项目术语和证据按需填写。摘要可用轻量 Markdown 拆成少量要点，但不保存 Agent 推理过程、长日志、完整代码和重复评级。代码定位优先使用文件加类/方法/符号，行号只作快照提示。通用技术词不解释，只定义项目内、业务内或 Agent 临时创造且人工可能不知道的词。

讨论消息使用独立的 `discussion-*` 命令，不再用 `COMMENT_ADDED / DESIGN_GUIDANCE` 塞进处理历史。`discussion-list` 默认只返回最近 20 条摘要，并支持 `--cursor` / `--since` 增量读取；正文通过 `discussion-get` 按 id 获取。页面“讨论”视图还会只读投影设计、Stage 和实现正式提交；这些提交仍以 Activity 作为唯一数据源并保留在处理历史，“全部”视图去重后按最新优先展示。Developer、Inspector 修正自己的讨论消息时直接 `discussion-amend`；待审核的设计、Stage、实现提交可用 `activity-amend`，一旦被审核就锁定。讨论达成一致后由 Inspector 用 `decision-record` 写入短结论并关联讨论；同一类型和作用域的新结论成为当前有效版本，历史版本只留审计。

用户与 Inspector 的当前 CLI 对话是需求澄清和关键设计确认入口，Issue/Web 只用于自动留档、回看和管理纠错。明确 Bug、唯一合理实现、普通代码细节或用户已明确允许的变化无需重复询问；新增持久化或基础设施、数据变更、对外行为变化、新依赖、范围扩大或多种影响不同的方案，必须先由 Inspector 在 CLI 用日常中文简短询问。用户回答后用 `reviewctl design confirm` 绑定当前设计，再用 `reviewctl design approve` 批准；设计修订后旧确认失效。其余设计在审批动态参数中声明 `confirmation: not-needed`。

## 设计与实现协作

设计职责按层划分：Inspector 用 `reviewctl design request` 负责 What / Why / Boundary / Architecture Direction / Acceptance——根因、应在哪一层解决、推荐和禁止的架构方向、不可破坏语义、方案必须回答的问题和验收条件；Developer 用 `reviewctl design submit` 在该框架内决定具体 How——修改哪些类和方法、数据流与 API/DB/状态落地、幂等并发事务、测试方案。Inspector 不下沉到具体代码实现，Developer 不重新决定已确定的架构方向；Developer 认为方向有事实错误时通过讨论反馈，由 Inspector 修订，不得静默改向。Inspector 用 `reviewctl design approve|reject` 绑定当前设计提交并明确批准或驳回，审批时检查方向遵守、根因解决、边界、Design Questions 回答、验收可验证、无必要复杂化和 scope change；实现方式与自身偏好不同不构成驳回理由。

设计深度随复杂度分级（规则见 `references/workflow.yaml` 的 `design_depth`）：SIMPLE 问题确认边界后直接实现，不要求 design-request 和 Stage Plan；NORMAL 问题设计至少覆盖 Root Cause、Boundaries、Design Questions、Acceptance；COMPLEX / HIGH-RISK 问题（跨模块职责、核心链路、状态机、并发事务一致性、持久化模型、数据迁移、对外行为、新依赖、大重构、高回归风险）还必须给出明确到架构层的 Architecture Direction（状态归属、source of truth、push/pull、生命周期管理、允许与禁止路径、必须幂等的路径），禁止“注意兼容”“考虑并发”这类空话。批准时必须选择 `direct` 或 `staged`；staged 会在同一事务中创建 Stage Plan、批准设计并默认激活 Stage 1。设计状态下 Developer 不得修改业务代码或提交实现。

```text
SIMPLE      Inspector(问题+边界) → Developer(实现) → Inspector(验证)
NORMAL      Inspector(四段式设计要求) → Developer(方案) → Inspector(审) → Developer(实现) → Inspector(验证)
COMPLEX     Inspector(五段式含架构方向) → Developer(方案) → Inspector(审/Stage Plan)
            → 分 Stage 实施与验收 → 最终实现 → 最终验证
```

实现审核失败时，若只是代码未按批准方案正确落地，则记录 `VERIFICATION_FAILED` 并回 `IN_PROGRESS`；若方向本身被新证据推翻，则转 `REDESIGN_REQUIRED`。转入后 Runtime 先唤醒 Inspector 并拒绝 Developer 直接重交方案：Inspector 必须重新检查 Root Cause、Architecture Direction、Boundaries、Acceptance 哪些判断失误，先用 `design-request` 提交修订后的架构级指导，Developer 再提交新方案。连续两次失败后 Inspector 必须主动重新判断失败属于实现还是设计，避免重复阅读与大范围返工。

复杂 Issue 可在设计批准前创建 Stage Plan。Stage 独立于 Issue 状态，按 `PLANNED → IN_PROGRESS → PENDING_REVIEW → APPROVED` 串行推进。Developer 在改码前先用 `reviewctl stage prepare` 声明影响范围和原因，历史保护项默认从既有 baseline 继承；完成后通过 `reviewctl stage submit` 提交 commit 和当前测试，命令自动关联 Git Diff 与上一轮阻断 finding。Inspector 默认从 `reviewctl stage show` 读取当前 Stage、合并后的有界保护约束和历史摘要；确需核验证据时再用 `reviewctl stage history` 读取完整 baseline。审核使用 BLOCKER/MUST/SHOULD/NIT 四级 finding，只有前两级阻断。通过时建立包含已验证行为、输入输出契约、业务语义和测试集合的 `PASSED` baseline，自动激活下一 Stage。第二轮起不得新增无关 SHOULD/NIT，新 BLOCKER/MUST 必须解释此前遗漏原因和实际风险；阻断项清零且所有验收通过后必须 PASS。若发现整案错误则用 `reviewctl stage redesign` 进入 `REDESIGN_REQUIRED`，旧计划完整保留。所有 Stage 通过后才允许 `reviewctl impl submit`，且仍需最终整体验证。简单 Issue 无需 Stage。

## Human 最终兜底

`INSPECTOR_CONFIRMATION_REQUIRED` 是 Developer 向 Inspector 请求技术边界的正常协作状态，不会通知 Human。`HUMAN_CONFIRMATION_REQUIRED` 是异常升级：只有 Inspector 在穷尽可得证据后，确认缺少只能由 Human 提供的关键业务事实，或存在重大且不可逆的数据安全风险时，才能用 `human-escalate` 进入。普通技术分歧、架构选择、方案驳回、实现或测试失败不得升级。

Human 使用 `human-confirmation-resolve` 记录业务边界或风险决定，恢复到 `DESIGN_REQUIRED`、`IN_PROGRESS`、`ON_HOLD`、`BLOCKED` 或 `CANCELLED`。Human 不能借此直接确认 Issue；后续设计、实现、验证和 `CONFIRMED` 仍由 Inspector 与 Developer 完成。未来若启用 Orchestrator，Resolver 应在该状态返回 `HUMAN / needs_human` 并暂停 Task 自动调度；本仓库当前升级不修改 Orchestrator。

## Git 更新后的行为

执行 `git pull` 或修改本地源文件不会直接改变 Agent 当前使用的版本。验证源码后执行 `python3 scripts/code-inspector-installer/install.py --force install`，安装器才会复制并替换平台目录中的完整 Skill 和运行工具。

## 多 Issue Runtime

启用 Thread Isolation 后，Supervisor 只保存 `(issue_key, operator_id) → thread_id`、固定身份、租约和事件等轻量调度数据；具体审核、实现、Diff/Evidence/测试分析由独立 Issue Thread 完成。Review Domain 的可执行状态变化会在同一事务写入 Runtime Event。Event 只作为唤醒信号：Supervisor 领取时重新计算当前 Issue Projection，同一 Issue/Role 的旧事件会标记为 `SUPERSEDED`，没有真实待办时不会调用模型；dispatch 使用事务内 Event row-id cutoff，晚于快照的新 Event 不会被误收敛。Resume 会再次检查 Projection，无待办时直接 `SKIPPED_STALE`，待办变化时使用最新 action。Projection revision 是 Issue 自身的单调 Working Set 版本，由数据库触发器覆盖 Issue、Activity、Discussion、Decision、Stage 和相关 Task 字段变化；领取时的 event revision 只用于追踪，Prompt、Compact 和 metrics 使用 Resume 时重读的 execution revision。普通 Action Turn 先且通常只调用一次 `reviewctl issue context <issue_key>` 获取同一 SQLite read snapshot 下的有界 Working Set（包括项目治理字段、Issue 初始 evidence/local terms 和最近 8 条讨论摘要），再按资源 id 懒加载明细；evidence 与 local terms 同样有数量和单项长度上限。`pending_action` 表示当前待办，`permitted_actions` 与 `exception_actions` 由 Runtime 权限唯一计算。每个 INIT/ACTION/COMPACT Turn 只记录 Token 数和 Review DB 子命令计数，不保存提示词、工具参数、返回正文或推理内容。App Server 适配层、Registry CLI、事件调度器、静默多目标 Watcher和兼容性探针位于 `scripts/`，开关集中在 `config/runtime.json`。

```bash
python3 scripts/issue-thread.py status
python3 scripts/code-inspector-supervisor.py status
python3 scripts/code-inspector-supervisor.py metrics --issue RI-EXAMPLE
python3 scripts/code-inspector-supervisor.py run
python3 scripts/capability-probe.py --write
```

Developer Thread 只获得 workspace-write 能力，并按项目路径加 Workspace Lock；需要多个 Developer 真正同时改同一仓库时，仍必须先分配独立 worktree/branch。

以下情况仍需要显式执行安装器：

- 修改源 `SKILL.md`、`agents/agent-roles.json`、机器 Agent 或角色绑定：重新执行 `install`，重新生成平台专属 `SKILL.md` 和绑定记录。
- 新增数据库迁移：执行 `migrate`。

```bash
python3 scripts/code-inspector-installer/install.py migrate
```

## 相关文件

- [SKILL.md](SKILL.md)：运行规则。
- [agents/agent-roles.json](agents/agent-roles.json)：此 Skill 专属的机器 Agent、角色、权限和绑定。
- [agents/openai.yaml](agents/openai.yaml)：Skill 的界面元数据。
- [references/activation.yaml](references/activation.yaml)：会话模式触发条件。
- [references/workflow.yaml](references/workflow.yaml)：任务与问题状态流转。
- [references/tool-contracts.yaml](references/tool-contracts.yaml)：数据库领域命令契约。
