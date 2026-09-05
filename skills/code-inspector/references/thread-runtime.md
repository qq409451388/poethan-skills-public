# Issue Thread Runtime

本文件约束 Code Inspector 的多 Issue 调度。`Review DB` 是业务状态真相，Thread 只保存推理上下文；Supervisor 只做 Registry、事件与 Dispatch，不读取 Diff、Evidence、测试日志或大段业务内容。

## 启用与降级

先读取 `config/runtime.json`。Multi-Thread 默认不激活，且使用双重授权：`thread_runtime.multi_thread.enabled=true` 只表示能力允许；用户还必须在当前 Session 明确要求开启。任一条件不满足时沿用既有单会话流程，不得 claim Event 或 start/resume Child Thread。配置关闭但用户要求开启时返回 `MULTI_THREAD_DISABLED_BY_CONFIG`；配置允许但当前 Session 未显式开启时保持 inactive。关闭只停止自动 Dispatch，保留 Mapping。

Session 激活时由安装绑定固化 `session_operator_id`、`session_role`、`session_agent_platform`。Supervisor claim 必须同时过滤 operator 与 role；Child Thread 的 operator/role 必须与 Session 完全相同，否则返回 `SESSION_SCOPE_VIOLATION`。Inspector Session 不得调度 Developer，Developer Session 也不得调度 Inspector；领域事务可以为另一身份产生 Event，但当前 Session 到此停止。Watch、Multi-Thread、Managed Compact 是独立授权；它们以及“持续到结束”都不允许创建或恢复 Codex Goal。

成功开启时只报告当前 Session 的 role、operator，以及“本 Session 只会调度该 operator 的 Issue Thread”。不得把激活状态写成全局开关；不同窗口可以分别 active/inactive。

`thread_runtime.enabled=false` 或 `isolation.enabled=false` 时同样沿用单会话流程；不得伪造 thread id。App Server 能力以 `scripts/capability_probe.py` 对本机当前版本的实际结果为准，禁止自动升级 Codex。

## Thread 与事务

默认业务键为 `(issue_key, operator_id)`；Mapping 同时固化 role、agent_platform、runtime_backend 与 thread_id。同一键只能有一个有效 Thread，CLI 的 `--role` 只做期望值校验，权限来源始终是安装器生成的 operator binding。首次 Dispatch 必须完成 `thread/start → Initialization Turn completed → persist mapping → 原始 Event Action Turn completed`；只有 Action Turn 成功后 Event 才能进入 DONE。初始化失败不得保存 Mapping，也不得自动创建第二个 Thread。

已有 Mapping 时必须 Resume 原 Thread并发送最小事件；Resume 使用 `excludeTurns=true`，然后让子 Thread重新读取 Review DB 的 Issue、Plan、Current Stage、最新必要 Activity、Evidence 和 Review Result。不得复制 Supervisor 会话历史。Issue reopen 优先 unarchive/resume 原 Thread。

初始化 Prompt 必须包含 operator_id、agent_platform、role、fixed_tool_path 和 issue_key。子 Thread 只使用固定工具，只处理绑定的 Issue 与角色，不得调用底层工具伪造身份，不得发现或调度其他 Issue。角色不明、Issue 不存在、Mapping 冲突、无法 Resume、锁失败、协议异常时 fail closed；非 Codex 平台当前记录为 `runtime_backend=external`，不得伪装成 Codex App Server Thread，也不得退回 Supervisor 执行业务工作。

## Supervisor 与事件

Review Domain 原子命令在更新 Issue/Stage/Activity 的同一个 SQLite Transaction 中写入 Runtime Event（Transactional Outbox）。该自动队列与 Manual Watch 完全独立：只有显式启用 Multi-Thread 的 Session 才可用 `supervisor.py run --session-identity <当前身份> --multi-thread` 消费当前 operator+role 的事件；空闲轮询只查询 SQLite、不调用 LLM。Manual Watch 仍只由用户显式开启，且不会隐式开启 Multi-Thread。

`scripts/supervisor.py` 维护轻量 Registry、幂等事件队列和 Lease。事件格式为：

```text
ACTION_REQUIRED
issue=RI-00497003
role=inspector
reason=STAGE_SUBMITTED
stage=3
```

同一 Issue+Operator 的事件按序 claim/消费；有 PROCESSING 前序事件时不得 claim 后续事件。不同 Issue 可在 `max_active_issue_threads` 限额内并行。Event 和 ACTIVE Thread 都有 worker、lease 与 heartbeat。stale Event 在确认尚未开始 Action 时才可有限重试；已经关联到 Thread 的不确定 Event 标为 AMBIGUOUS。stale ACTIVE Thread 会读取 App Server 状态与 Review DB 后转 PAUSED，绝不自动重放未知副作用 Turn。Issue 可在 ACTIVE/WAITING 间反复切换，不要求前一个 Issue 完成。Supervisor 输出仅含调度字段。

## 锁与 Workspace

Dispatch Lock 串行化同一 `(issue_key, operator_id)`，并完整覆盖 lookup、可选初始化和原始 Action Turn；Thread Execution Lock 覆盖 resume、turn、compact、archive；获取失败返回 BUSY/RETRYABLE，不继续也不创建新 Thread。Inspector Thread 使用只读 sandbox。Developer Thread 使用 workspace-write，但同一项目路径必须持有 Workspace Lock；需要真正并行写入同一仓库时，先由人或外层系统分配独立 branch/worktree。Thread 隔离不等于文件系统隔离。

所有有副作用的 App Server 操作遇到不确定结果不得自动重放，以免重复 Thread 或重复动作。失败应将对应 Mapping 置为 PAUSED/FAILED 并记录错误；恢复前先核实 Thread 与 Review DB。

## Managed Compact

仅当以下条件全部满足才主动 Compact：功能和 capability flag 已启用；本机实测支持 compact 后继续及跨进程 resume；App Server 提供可靠的 `totalTokens/modelContextWindow`；上一 Stage 已 APPROVED 且下一 Stage 已存在；usage 达到 threshold；该 Stage 从未尝试过 Managed Compact。

顺序固定为：Stage Result 已持久化 → 检查 usage → compact → 重新读取 Skill/角色/Issue/Plan/Stage/关键 Activity → 进入下一 Stage。Compact 失败只记录一次并继续 Thread，不在同一 Stage 无限重试。不得以字符数、消息数或 Turn 数估算 80%。Managed Compact 与 Codex Internal Auto Compact 不同，关闭前者不影响后者。

## CLI 路由

- `scripts/issue-thread.py dispatch|start|resume|compact|archive --session-identity <当前身份> --multi-thread`；`status` 为只读查询
- `scripts/code-inspector-supervisor.py run|dispatch-pending --session-identity <当前身份> --multi-thread`；其余为入队、只读或人工恢复命令
- `scripts/capability-probe.py`：本机协议与跨进程/compact 探测
- `scripts/task-watcher.py`：一个静默 Shell watcher 观察多个显式 WAITING Issue

所有子命令只输出最小调度结果。具体 Issue 推理始终留在对应 Issue Thread。
