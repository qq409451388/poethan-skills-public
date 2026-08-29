---
name: code-inspector
description: 在用户明确开启代码检查模式后，按安装时分配的 developer 或 inspector 逻辑身份处理代码检查任务、问题、整改、验证和确认。
---

# Code Inspector

仅在用户明确开启后执行本流程；启动命令与角色选择规则见 `references/activation.yaml`。

安装后的 Skill 会生成当前平台可用的逻辑身份、角色能力、会话角色选择器和固定工具入口。会话角色在启动时确定，并保持到退出；不得根据后续任务自动切换身份。一个实际 Agent 可以承担多个角色，但每个角色必须使用不同 alias；同一角色有多个 alias 时，安装配置必须指定唯一默认身份。只使用当前会话角色对应身份的入口，不直接访问 SQLite 或执行 SQL。

执行前按场景读取本 Skill 内的参考文件：

- 审核、整改、状态变更：`references/workflow.yaml`
- 调用数据库工具：`references/tool-contracts.yaml`
- 选择审核等级：`references/review-levels.yaml`
- 导出报告：`references/report-schema.yaml`

通用规则：task 是长期稳定的检查目标；扫描先收集候选问题，按 `workflow.yaml` 完成跨模块数据流与覆盖面回查，再去重并批量创建 issue；默认聊天只输出简短摘要，完整报告仅在用户明确要求时导出。

活动内容以清晰可读为准：简短结论使用单行纯文本；多个要点可以直接换行；只有包含代码、命令、结构化列表或复杂层级时才使用 Markdown。不要为了格式化给普通短句添加标题、列表符号或代码块。
