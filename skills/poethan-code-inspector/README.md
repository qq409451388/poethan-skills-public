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

Inspector 输出抽象 `difficulty`（1–5），本机 `~/.agent-review/config/agent-routing.yml` 手填执行配置：

```yaml
version: 2
style: 1
profiles:
- id: codex-gpt-6-astra
  agent: codex
  model: gpt-6-astra
  reasoning: high
  level: 5
```

根参数 `style` 选择等级标签外观：`1` 曜石渐变、`2` 极光玻璃、`3` 金属勋章；省略时默认 `1`，只接受整数 `1..3`。它只影响展示，不改变候选筛选和 Assignment 有效性。

YAML 中的每条 Profile 都参与推荐，筛选条件为 `level >= difficulty`。配置不存在或无效时推荐为空，审核开发流程继续可用。Model/Reasoning 校验范围来自 [config/agent-capabilities.yml](config/agent-capabilities.yml)，未收录模型使用通用档位。

WebApp `/routing` 按 Agent 分组只读展示执行配置；新增、删除和等级调整统一编辑 YAML。保存先完整校验，再原子替换文件。旧 `enabled: true` 读取时忽略，保存时移除；旧 `enabled: false` 明确报错，必须删除对应条目，避免静默启用。

推荐 `recommendedExecutors` 在读取时按当前 YAML 计算，不写数据库。正式 Assignment 保存所选 Profile 的快照；配置删除、模型或 Reasoning 变化、等级降低或不满足 difficulty 时返回 `STALE`，不会自动改选或删除历史分配。Assignment 用于记录执行选择，不自动启动或切换 Agent 模型。

## 安装

macOS / Linux 在仓库根目录执行：

```bash
python3 scripts/code-inspector-installer/install.py install
python3 scripts/code-inspector-installer/install.py verify

# 安装后把角色命令加入 PATH（一次性）：
echo 'export PATH="$HOME/.agent-review/bin:$PATH"' >> ~/.zshrc
command -v cictl-dev   # 应输出 ~/.agent-review/bin/cictl-dev
command -v cictl-insp  # 应输出 ~/.agent-review/bin/cictl-insp
```

Windows 在 PowerShell 中执行：

```powershell
python scripts/code-inspector-installer/install.py install
python scripts/code-inspector-installer/install.py verify
```

支持 macOS、Linux 和 Windows。安装器先把完整内容复制到目标目录旁的临时路径，准备完成后再替换旧版本；失败时恢复旧版本。Agent 目录不使用软链接，仓库中的半成品修改不会立即影响正在使用的 Skill。

安装器会创建：

```text
~/.codex/skills/poethan-code-inspector/       # 为 Codex 生成的角色 Skill
~/.trae-cn/skills/poethan-code-inspector/     # 为 Trae-CN 生成的角色 Skill
~/.claude/skills/poethan-code-inspector/      # 为 Claude 生成的角色 Skill
~/.dsh/skills/poethan-code-inspector/         # 为 DeepSeek Harness 生成的角色 Skill

以上目录中的 references/ 复制自：
<repository>/skills/poethan-code-inspector/references

~/.agent-review/bin/cictl-dev       # Developer 角色命令（全平台通用，加入 PATH 后可用）
~/.agent-review/bin/cictl-insp      # Inspector 角色命令（全平台通用，加入 PATH 后可用）
~/.agent-review/bin/cictl.py        -> 复制自 <repository>/scripts/code-inspector-installer/runtime/cictl.py；只被角色命令加载，不直接执行
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
```

修改这些源文件后重新执行安装，安装器会一次性替换 Agent 使用的完整副本，不需要同步修改生成器中的 Skill 文案。

如果目标路径已有旧版普通文件，安装器会拒绝覆盖。确认该目录是旧安装器内容后可使用：

```bash
python3 scripts/code-inspector-installer/install.py --force install
```

安装器只识别 `poethan-code-inspector` 这个名字。旧名 `code-inspector` 的触发词不再生效；各平台下由本安装器生成的旧 `code-inspector` 目录会在安装时自动删除，同名但不是本安装器创建的内容保持不动。

## 使用方式：显式开启会话模式

Code Inspector 默认不创建任务，也不写数据库。必须先在当前会话说：

```text
进入代码检查模式
```

或：

```text
$poethan-code-inspector start
```

当当前 Agent 只配置一个角色时，`start` 可以不带参数。配置多个角色时必须显式选择：

```text
$poethan-code-inspector start dev
$poethan-code-inspector start insp
```

`/poethan-code-inspector` 形式也兼容。角色在启动时锁定，不会根据后续任务自动切换；需切换时先退出再重新启动。开启后，明确要求“开始审核”“创建审核任务”“处理审核问题”“提交实现”“验证修复”或“最终确认”时，才进入对应工作流。退出模式使用：

```text
退出代码检查模式
```

或：

```text
$poethan-code-inspector stop
```

完整触发规则见 [references/activation.yaml](references/activation.yaml)。

## 任务与问题的边界

Task 有两类：`REVIEW` 是一次边界明确的检查治理目标，继续沿用项目、等级、目标、范围和基线 identity；`CONTINUOUS` 是可持续数月的治理主题，identity 不包含 `baseline_ref`，代码基线变化仍复用同一 Task。类型创建后不可修改。`CONTINUOUS` 的全部 Issue 关闭后 Task 仍保持活动，只有 Inspector/Human 显式关闭或取消才结束。Task 显式转为 `CLOSED` 时，尚未终结的 Issue 会在同一事务中转为 `CANCELLED`；已确认或已取消的 Issue 不变。Task 的其他状态变更不会同步 Issue。

Inspector 修改 Task 状态时遵守标准状态机。Human 可将 Task 设为任一合法状态，包括重新打开 `CLOSED / CANCELLED` 任务；操作仍通过 `task-update-status` 记录审计。

同样，Human 可通过 `issue-update-status` 将 Issue 设为任一合法状态，包括进入或离开 `HUMAN_CONFIRMATION_REQUIRED`、直接 `CONFIRMED` 或重新打开终态。操作记录 `STATUS_CHANGED` 与审计；Inspector/Developer 仍遵守原有状态机和验证前置条件。

“继续审核、扫描项目、专项检查”属于 `scan`，无论 Task 类型都必须执行 coverage closure、跨模块回查、补扫和完整去重。“把这个线上 Bug 记入长期任务”属于 `report`，只需核实证据、确认成立、去重并创建 Candidate/Issue，不要求重新扫描整个项目。

扫描时先收集候选问题，完成范围扫描后再统一去重和落库。相同根因、修复边界和风险链路只能是一个 issue，多个位置应作为它的证据。未终态 issue 重复出现时不新建；已经确认的问题再次出现时才创建新的 issue 和版本。

默认聊天输出仅包含 task 编号、本轮新增/重复问题数、最高风险和下一步。只有明确要求导出时才输出完整 Markdown 或 JSON 报告。

正式 Issue 默认只需要标题、短摘要、维度和严重度；完成标准、技术补充、本项目术语和证据按需填写。摘要可用轻量 Markdown 拆成少量要点，但不保存 Agent 推理过程、长日志、完整代码和重复评级。代码定位优先使用文件加类/方法/符号，行号只作快照提示。通用技术词不解释，只定义项目内、业务内或 Agent 临时创造且人工可能不知道的词。

讨论消息使用独立的 `discussion-*` 命令，不再用 `COMMENT_ADDED / DESIGN_GUIDANCE` 塞进处理历史。`discussion-list` 默认只返回最近 20 条摘要，并支持 `--cursor` / `--since` 增量读取；正文通过 `discussion-get` 按 id 获取。页面“讨论”视图还会只读投影设计、Stage 和实现正式提交；这些提交仍以 Activity 作为唯一数据源并保留在处理历史，“全部”视图去重后按最新优先展示。Developer、Inspector 修正自己的讨论消息时直接 `discussion-amend`；待审核的设计、Stage、实现提交可用 `activity-amend`，一旦被审核就锁定。仅独立讨论结论未被正式动作记录时，由 Inspector 用 `decision-record` 写入并关联讨论；同一类型和作用域的新结论成为当前有效版本，历史版本只留审计。

## 方案、派工与验收

默认闭环：Inspector 核实需求、事实与原因，形成可执行方案并完成必要讨论 → design dispatch → Developer 实现与验证、impl submit → Inspector impl review 原子验收并收口。

方案由 Inspector 负责；Developer 对命名、局部组织等自主决定，但不得擅自改向。发现前提不成立只需提供前提、证据、影响和未知项，由 Inspector 澄清或修订。方向变化重新派工，旧授权与旧 Stage Plan 失效。

简单方案几句话即可，包含 content、boundaries、acceptance、execution_mode。方案复杂度与 direct/staged 独立；中间成果有独立验收价值才分 Stage。无需选择时一次 dispatch 开工；有范围/风险选择时先 design request 保存 Inspector 草稿，再 preview/confirm 绑定真实回答，dispatch 引用 draft_id 与 confirmation_ids，不重抄方案或重复到 Web 确认。

```sh
cictl-insp design dispatch RI-1 @plan.json
cictl-dev impl submit RI-1 --authorization-id 123 --content '实现与验证结果' @evidence.json
cictl-insp impl review RI-1 @review.json
```

context 提供有效授权 ID。Runtime 在状态、Stage、实现提交与收口入口检查授权，不能只改 IN_PROGRESS 状态就开工；这些检查不是文件系统隔离。历史真实批准仍保留兼容入口，不存在批准的旧任务不会自动放行。没有新表、新状态或新调度平台。

新派工 Stage 继承方案边界和 baseline，不再强制额外 stage prepare；旧计划保留准备约束。Stage 审核的四级 findings、Gate、baseline 和自动激活复用。全部 Stage 通过后可引用有效证据，保留轻量最终交付和整体/集成验收。

impl review 绑定当前 submission_id、submission_revision、authorization_id，requirements 核对原始需求，checks 使用 AC-1..AC-n。通过关闭，fix 沿原方案修复，replan 由 Inspector 重新规划。只有 SHOULD/NIT 不阻断；照方案实现却不满足需求也不能通过。快照、依赖、环境和验证范围仍适用才复用证据；已变更需说明依据或补验，不机械重跑。

双方遵守最小充分设计：新增机制必须有具体收益和有依据的失败场景，说明维护成本，不为未来假设无限加固，不吞异常或随意降级。局部精简由 Developer 自主；方向变化先由 Inspector 判断，必要时询问 Human，不新增长审批清单。

短记录可复用正文作摘要，长正文才另给人话摘要；讨论可自动关联话题；正式动作已生成结论就不重复 decision add。派工/验收可附进度，最终关闭自动100。必要审计继续由 Runtime 自动生成。

## Human 最终兜底

`INSPECTOR_CONFIRMATION_REQUIRED` 是 Developer 向 Inspector 请求技术边界的正常协作状态，不会通知 Human。`HUMAN_CONFIRMATION_REQUIRED` 是异常升级：只有 Inspector 在穷尽可得证据后，确认缺少只能由 Human 提供的关键业务事实，或存在重大且不可逆的数据安全风险时，才能用 `human-escalate` 进入。普通技术分歧、架构选择、方案驳回、实现或测试失败不得升级。

升级必须自包含：每个选项都要给出 `label` 与 `impact`，并建议补 `purpose`（改动目的）与 `reason`（原因），附已验证事实与未知事实；只给标题的选项会被 Runtime 拒绝。因为 Human 通常没有全程跟进，升级后 Inspector 必须运行 `human brief <issue_key>` 并把输出贴进对话——它输出一段人话简报（为什么必须你定、已验证/未知事实、选项与影响、推荐、你只需回答什么），没有待决升级时则输出该 Issue 的最近进展摘要。同一份决策信息也随 `issue context` 的 `human_request` 返回，WebApp 的「最终边界 / 安全确认」会展示同样的选项与影响。Human 的决定仍然只在 WebApp 提交。

Human 可使用 `human-confirmation-resolve` 原子记录业务边界或风险决定，并恢复到 `DESIGN_REQUIRED`、`IN_PROGRESS`、`ON_HOLD`、`BLOCKED` 或 `CANCELLED`；也可用通用状态命令直接指定任一合法状态。未来若启用 Orchestrator，Resolver 应在该状态返回 `HUMAN / needs_human` 并暂停 Task 自动调度；本仓库当前升级不修改 Orchestrator。

## Git 更新后的行为

执行 `git pull` 或修改本地源文件不会直接改变 Agent 当前使用的版本。验证源码后执行 `python3 scripts/code-inspector-installer/install.py --force install`，安装器才会复制并替换平台目录中的完整 Skill 和运行工具。

## 会话与上下文

Agent 在当前会话处理指定 Issue，不创建或恢复独立 Thread，不自动调度其他角色。新会话显式选择角色后，用 `issue context <issue_key>` 读取有界 Working Set；只有需要正文时才按资源 id 读取明细。状态与审核权限仍由领域命令校验。

Watch 只在用户明确要求时使用 Shell 静默观察指定目标，命中后由当前会话处理。FastMode 由 Human 协调开发，Inspector 串行检查指定 Issue 并记录结论。历史调度表保留但不再读写。

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

### 手动重新评估进度

已开启 Inspector 会话后输入 `issue progress RI-XXX`；当前唯一绑定一个 Issue 时可省略编号，输入 `issue progress`。Inspector 读取现有审核、阶段验收与验证证据，自主给出百分比和依据并保存。也接受 `$poethan-code-inspector issue progress RI-XXX`。这与底层写入命令 `cictl-insp issue progress RI-XXX 35 --reason "依据"` 不同，用户不需要手填百分比。
