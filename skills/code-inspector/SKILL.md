---
name: code-inspector
description: 在用户明确开启代码检查模式后，按安装时分配的 developer 或 inspector 逻辑身份处理代码检查任务、问题、整改、验证和确认。
---

# Code Inspector

仅在用户明确开启后执行本流程；启动命令与角色选择规则见 `references/activation.yaml`。

安装后的 Skill 会生成当前平台可用的逻辑身份、角色能力、会话选择器和固定工具入口。会话角色在启动时确定并保持到退出，不得自动切换身份；只使用当前身份的固定工具，不直接访问 SQLite 或执行 SQL。

面向人的 Issue、讨论、提交、审核、验证和报告默认使用简明中文：先讲现象、影响、结论、验证结果和下一步，再补必要技术证据，确保测试人员和项目负责人无需理解内部实现也能读懂。新写代码注释也默认用中文解释业务目的和约束。Git 提交标题和正文同样默认使用中文；仓库采用 Conventional Commits 时可保留 `feat/fix/refactor(scope)` 等固定前缀，但冒号后的摘要和正文必须用中文说明业务变化，例如 `refactor(analytics): 统一淘宝数据源边界`，不得只写英文摘要。若项目已有强制语言规范则遵循项目规范。

Multi-Thread 默认关闭。只有 `config/runtime.json` 允许且用户在当前 Session 明确要求开启时，才可启动按当前 `session_operator_id + session_role` 限定的 Supervisor。它只能 claim、start、resume 当前身份的 Event/Thread；跨 Role 或跨 Operator 一律以 `SESSION_SCOPE_VIOLATION` 失败。Watch 与 Multi-Thread 分别授权，任何模式都禁止创建或恢复 Codex Goal。

激活后必须读取：

- 所有角色：`references/core-workflow.md`
- 当前角色：`references/role-workflows.md` 中对应的 Developer 或 Inspector 章节

再按场景读取：

- 状态机与状态变更：`references/workflow.yaml`
- 数据库工具参数：`references/tool-contracts.yaml`
- 审核等级：`references/review-levels.yaml`
- 用户明确要求持续观察或停止观察：`references/watch-mode.md`
- 多 Issue 调度、Issue Thread 或 Managed Compact：`references/thread-runtime.md`
- 用户明确要求导出报告：`references/report-schema.yaml`

不要因为流程进入等待状态而读取或启动 Watch Mode。
