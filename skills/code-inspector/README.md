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

`inspector` 同时承担验证职责，不使用独立 `verifier` Agent。

同一个实际 Agent 可以承担多个角色，但每个角色必须配置不同 alias，以便权限校验和审计日志明确区分逻辑身份。同一平台、同一角色配置多个 alias 时，必须且只能有一个绑定设置 `"default": true`。角色的会话启动参数由 `session_selector` 配置；当前 `dev` 表示 developer，`insp` 表示 inspector。

## 安装

macOS / Linux 在仓库根目录执行：

```bash
python3 scripts/code-inspector-installer/install.py install
python3 scripts/code-inspector-installer/install.py verify
```

Windows 在 PowerShell 中执行：

```powershell
python scripts/code-inspector-installer/install.py install
python scripts/code-inspector-installer/install.py verify
```

支持 macOS、Linux 和 Windows。安装器会优先使用软链接，使仓库更新立即生效；如果
Windows 未启用开发者模式或当前用户无创建软链接权限，则自动复制对应文件或目录，
无需管理员权限。

安装器会创建：

```text
~/.codex/skills/code-inspector/       # 为 Codex 生成的角色 Skill
~/.trae-cn/skills/code-inspector/     # 为 Trae-CN 生成的角色 Skill

以上目录中的 references/ 链接或复制自：
<repository>/skills/code-inspector/references

~/.agent-review/bin/review-db.py
  -> 链接或复制自 <repository>/scripts/code-inspector-installer/runtime/review_db.py
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

使用软链接的安装环境修改这些 reference 后立即生效；软链接不可用而回退为复制的环境需要重新执行安装，但不再需要同步修改安装器中的 Skill 文案。

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

讨论消息使用独立的 `discussion-*` 命令，不再用 `COMMENT_ADDED / DESIGN_GUIDANCE` 塞进处理历史。页面“讨论”视图还会只读投影设计、Stage 和实现正式提交；这些提交仍以 Activity 作为唯一数据源并保留在处理历史，“全部”视图去重后按最新优先展示。Developer、Inspector 修正自己的讨论消息时直接 `discussion-amend`；待审核的设计、Stage、实现提交可用 `activity-amend`，一旦被审核就锁定。讨论达成一致后由 Inspector 用 `decision-record` 写入短结论并关联讨论；同一类型和作用域的新结论成为当前有效版本，历史版本只留审计。

## 设计与实现协作

复杂或高风险 Issue 应在编码前进入设计阶段：Inspector 用 `design-request` 写清根因、约束、不可破坏语义、风险、推荐方向和方案必须回答的问题；Developer 用 `design-submit` 提交具体类、方法、数据流、兼容与测试方案；Inspector 用 `design-review` 明确批准或驳回。设计状态下 Developer 不得修改业务代码或提交实现。

实现审核失败时，若只是代码未按批准方案正确落地，则记录 `VERIFICATION_FAILED` 并回 `IN_PROGRESS`；若方向本身被新证据推翻，则转 `REDESIGN_REQUIRED`，强制重新走方案审核。连续两次失败后 Inspector 必须主动重新判断失败属于实现还是设计，避免重复阅读与大范围返工。

复杂 Issue 可在设计批准前创建 Stage Plan。Stage 独立于 Issue 状态，按 `PLANNED → IN_PROGRESS → PENDING_REVIEW → APPROVED` 串行推进。Developer 在改码前先用 `stage-prepare` 声明影响范围、原因和历史保护项，完成后通过 `stage-submit` 提交 commit、Diff、当前测试、历史累计回归与代码证据。Inspector 按当前验收标准及所有历史 Stage baseline 复核，使用 BLOCKER/MUST/SHOULD/NIT 四级 finding；只有前两级阻断。通过时建立包含已验证行为、输入输出契约、业务语义和测试集合的 `PASSED` baseline，自动激活下一 Stage。第二轮起不得新增无关 SHOULD/NIT，新 BLOCKER/MUST 必须解释此前遗漏原因和实际风险；阻断项清零且所有验收通过后必须 PASS。若发现整案错误则显式进入 `REDESIGN_REQUIRED`，旧计划完整保留。所有 Stage 通过后才允许 `implementation-submit`，且仍需最终整体验证。简单 Issue 无需 Stage。

## Human 最终兜底

`INSPECTOR_CONFIRMATION_REQUIRED` 是 Developer 向 Inspector 请求技术边界的正常协作状态，不会通知 Human。`HUMAN_CONFIRMATION_REQUIRED` 是异常升级：只有 Inspector 在穷尽可得证据后，确认缺少只能由 Human 提供的关键业务事实，或存在重大且不可逆的数据安全风险时，才能用 `human-escalate` 进入。普通技术分歧、架构选择、方案驳回、实现或测试失败不得升级。

Human 使用 `human-confirmation-resolve` 记录业务边界或风险决定，恢复到 `DESIGN_REQUIRED`、`IN_PROGRESS`、`ON_HOLD`、`BLOCKED` 或 `CANCELLED`。Human 不能借此直接确认 Issue；后续设计、实现、验证和 `CONFIRMED` 仍由 Inspector 与 Developer 完成。未来若启用 Orchestrator，Resolver 应在该状态返回 `HUMAN / needs_human` 并暂停 Task 自动调度；本仓库当前升级不修改 Orchestrator。

## Git 更新后的行为

参考规则和数据库工具优先通过软链接安装，因此执行 `git pull` 后，链接安装的内容会立即更新。Windows 因权限限制回退为复制时，拉取更新后需要重新执行 `install`；如果源文件已经变化，使用 `--force install`。平台目录中的 `SKILL.md` 是根据角色配置生成的固化入口，不是软链接。

## 多 Issue Runtime

启用 Thread Isolation 后，Supervisor 只保存 `(issue_key, role) → thread_id`、状态和事件等轻量调度数据；具体审核、实现、Diff/Evidence/测试分析由独立 Issue Thread 完成。App Server 适配层、Registry CLI、事件调度器、静默多目标 Watcher和兼容性探针位于 `scripts/`，开关集中在 `config/runtime.json`。

```bash
python3 scripts/issue_thread.py status
python3 scripts/supervisor.py status
python3 scripts/capability_probe.py
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
