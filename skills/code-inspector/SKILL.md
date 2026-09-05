---
name: code-inspector
description: 在用户明确开启代码检查模式后，按安装时分配的 developer 或 inspector 逻辑身份处理代码检查任务、问题、整改、验证和确认。
---

# Code Inspector

仅在用户明确开启后执行本流程；启动命令与角色选择规则见 `references/activation.yaml`。

安装后的 Skill 会生成当前平台可用的逻辑身份、角色能力、会话选择器和固定工具入口。会话角色在启动时确定并保持到退出，不得自动切换身份；只使用当前身份的固定工具，不直接访问 SQLite 或执行 SQL。

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
