# Code Inspector

本机代码检查协作 Skill。它通过本地 SQLite 保存任务、问题、活动记录和审计日志；数据库只能经领域命令访问，Agent 不直接执行 SQL。

## 角色分工

默认绑定定义在 [agents/agent-roles.json](agents/agent-roles.json)：

| 机器 Agent | 平台 | Code Inspector 角色 |
| --- | --- | --- |
| `codex-dev` | Codex | `developer`：分析、修改代码、提交设计和实现 |
| `trae-inspector` | Trae-CN | `inspector`：创建任务和问题、验证修复、最终确认 |

`inspector` 同时承担验证职责，不使用独立 `verifier` Agent。

同一个实际 Agent 可以承担多个角色，但每个角色必须配置不同 alias，以便权限校验和审计日志明确区分逻辑身份。同一平台、同一角色配置多个 alias 时，必须且只能有一个绑定设置 `"default": true`。

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
/code-inspector start
```

开启后，明确要求“开始审核”“创建审核任务”“处理审核问题”“提交实现”“验证修复”或“最终确认”时，才进入对应工作流。退出模式使用：

```text
退出代码检查模式
```

或：

```text
/code-inspector stop
```

完整触发规则见 [references/activation.yaml](references/activation.yaml)。

## 任务与问题的边界

`task` 是长期、稳定的检查目标，例如“订单模块稳定性检查”，不是一次扫描、一次聊天或一个问题。相同项目、目标、范围和基线下的继续扫描、修复复核和证据补充必须复用未关闭 task；默认列表不会返回 `CLOSED` task。

扫描时先收集候选问题，完成范围扫描后再统一去重和落库。相同根因、修复边界和风险链路只能是一个 issue，多个位置应作为它的证据。未终态 issue 重复出现时不新建；已经确认的问题再次出现时才创建新的 issue 和版本。

默认聊天输出仅包含 task 编号、本轮新增/重复问题数、最高风险和下一步。只有明确要求导出时才输出完整 Markdown 或 JSON 报告。

## Git 更新后的行为

参考规则和数据库工具优先通过软链接安装，因此执行 `git pull` 后，链接安装的内容会立即更新。Windows 因权限限制回退为复制时，拉取更新后需要重新执行 `install`；如果源文件已经变化，使用 `--force install`。平台目录中的 `SKILL.md` 是根据角色配置生成的固化入口，不是软链接。

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
