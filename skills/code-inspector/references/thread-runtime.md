# Issue Thread Runtime

本文件约束 Code Inspector 的多 Issue 调度。`Review DB` 是业务状态真相，Thread 只保存推理上下文；Supervisor 只做 Registry、事件与 Dispatch，不读取 Diff、Evidence、测试日志或大段业务内容。

## 启用与降级

先读取 `config/runtime.json`。`thread_runtime.enabled=false` 或 `isolation.enabled=false` 时沿用既有单会话流程；不得伪造 thread id。Watch、Isolation、Managed Compact 是三个独立开关。App Server 能力以 `scripts/capability_probe.py` 对本机当前版本的实际结果为准，禁止自动升级 Codex。

## Thread 与事务

默认键为 `(issue_key, role)`，同一键只能有一个有效 Thread。首次 Dispatch 必须通过 `scripts/issue_thread.py start` 完成 `thread/start → Initialization Turn completed → persist mapping` 整体事务；初始化失败不得保存 Mapping，也不得自动创建第二个 Thread。

已有 Mapping 时必须 Resume 原 Thread并发送最小事件；Resume 使用 `excludeTurns=true`，然后让子 Thread重新读取 Review DB 的 Issue、Plan、Current Stage、最新必要 Activity、Evidence 和 Review Result。不得复制 Supervisor 会话历史。Issue reopen 优先 unarchive/resume 原 Thread。

子 Thread 只处理绑定的 Issue 与角色，不得发现或调度其他 Issue。角色不明、Issue 不存在、Mapping 冲突、无法 Resume、锁失败、协议异常时 fail closed；不得退回 Supervisor 执行业务工作。

## Supervisor 与事件

`scripts/supervisor.py` 维护轻量 Registry 和幂等事件队列。事件格式为：

```text
ACTION_REQUIRED
issue=RI-00497003
role=inspector
reason=STAGE_SUBMITTED
stage=3
```

同一 Issue+Role 的事件按序消费；不同 Issue 可在 `max_active_issue_threads` 限额内并行。Issue 可在 ACTIVE/WAITING 间反复切换，不要求前一个 Issue 完成。Supervisor 输出仅含 issue、role、thread、status、next_action、last_event 等调度字段。

## 锁与 Workspace

Dispatch Lock 串行化同一 `(issue_key, role)`；Thread Execution Lock 覆盖 resume、turn、compact、archive；获取失败返回 BUSY/RETRYABLE，不继续也不创建新 Thread。Inspector Thread 使用只读 sandbox。Developer Thread 使用 workspace-write，但同一项目路径必须持有 Workspace Lock；需要真正并行写入同一仓库时，先由人或外层系统分配独立 branch/worktree。Thread 隔离不等于文件系统隔离。

所有有副作用的 App Server 操作遇到不确定结果不得自动重放，以免重复 Thread 或重复动作。失败应将对应 Mapping 置为 PAUSED/FAILED 并记录错误；恢复前先核实 Thread 与 Review DB。

## Managed Compact

仅当以下条件全部满足才主动 Compact：功能和 capability flag 已启用；本机实测支持 compact 后继续及跨进程 resume；App Server 提供可靠的 `totalTokens/modelContextWindow`；上一 Stage 已 APPROVED 且下一 Stage 已存在；usage 达到 threshold；该 Stage 从未尝试过 Managed Compact。

顺序固定为：Stage Result 已持久化 → 检查 usage → compact → 重新读取 Skill/角色/Issue/Plan/Stage/关键 Activity → 进入下一 Stage。Compact 失败只记录一次并继续 Thread，不在同一 Stage 无限重试。不得以字符数、消息数或 Turn 数估算 80%。Managed Compact 与 Codex Internal Auto Compact 不同，关闭前者不影响后者。

## CLI 路由

- `scripts/issue_thread.py dispatch|start|resume|status|compact|archive`
- `scripts/supervisor.py enqueue|dispatch-pending|status`
- `scripts/capability_probe.py`：本机协议与跨进程/compact 探测
- `scripts/task_watcher.py`：一个静默 Shell watcher 观察多个显式 WAITING Issue

所有子命令只输出最小调度结果。具体 Issue 推理始终留在对应 Issue Thread。
