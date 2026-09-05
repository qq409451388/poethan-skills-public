# Manual Watch Mode

Watch Mode 是用户显式创建的、单目标、一次性、低输出观察。它默认关闭；流程进入等待状态不构成开启授权。

## 何时进入

只有用户明确使用“持续观察”“帮我盯着”“watch”等意图，并且指出要观察的任务时，才解析 Watch Specification：

```text
watch_target   = 唯一的 Issue、Task、Stage 或进程
query_method   = role tool 的 watch-probe，或 Shell 进程退出
expected_event = 哪个状态或哪个新 Activity/进程事件需要当前 Agent 行动
role           = 当前锁定角色（存在时）
continuation   = one-shot | until-target-terminal
interval       = 120 seconds（用户未另行指定时）
```

自然语言不要求固定格式。可以从已读取的当前 Issue、active Stage、角色和状态机推导字段，无需让用户重复；不能可靠推导时不得猜测。

启动前必须同时确定：观察什么、如何查最新状态、什么变化需要行动。任何一项缺失或互相冲突都不启动 Shell watcher，并明确指出缺失项后暂停 Watch。例如只说“帮我盯着”时缺少目标；只说观察某 Issue、但没有状态机上下文也没有唤醒条件时，缺少 expected_event。

Watch 不授权切换角色。若用户要求唤醒后由 Inspector 审核，但会话锁定为 Developer，说明角色冲突并不启动；用户显式切换后才能创建对应 Watch。

## 条件映射

- Issue 状态：`--kind issue-status`；例如 Inspector 等待 `DESIGN_PENDING_REVIEW` 或 `IMPLEMENTED_PENDING_REVIEW`。
- Stage 状态：`--kind stage-status`；例如 Inspector 等待指定 Stage 的 `PENDING_REVIEW`。
- 新工作流事件：`--kind activity`，并以启动前最新 Activity id 作为 `--after-activity-id`；例如 Developer 等待新的 `STAGE_REJECTED`。不得只看一个可能在启动时已成立的宽泛状态来代替“新事件”。
- Task 状态：`--kind task-status` 并精确匹配 task key。
- 已知 PID 的外部进程：`--kind process`，在进程结束时唤醒。若测试由当前 Agent 启动，优先直接等待其 Shell execution session 的退出事件，以便取得真实 exit code；不要另做状态轮询。

先用当前角色固定工具做一次只读查询，验证目标存在、查询可执行，并记录 Activity cursor 等基线。不要为了补齐无效描述而创建 Issue、Stage 或 Activity。

## 启动与静默等待

安装后的 watcher 位于 `scripts/watch.py`。校验通过后，用当前角色的固定工具路径启动它，并保留 Shell execution session id：

```text
python <skill>/scripts/watch.py \
  --kind stage-status \
  --tool <skill>/tools/review-db-<alias>.py \
  --target RI-00497003 \
  --stage 2 \
  --expect PENDING_REVIEW \
  --role inspector
```

Watcher 默认每 120 秒通过固定角色工具的 `watch-probe` 查询一次。该探针只返回最小状态且不写读取审计，避免长期轮询造成审计表膨胀；Watcher 不得直接访问数据库。状态未命中时 stdout/stderr 均保持空；查询 JSON、Stage、Activity、Diff 和日志不得进入对话。查询错误写入系统临时目录；连续三次查询失败只发出 `WATCH_QUERY_FAILED` 最小事件，由 Agent 重新诊断。

Watch Mode 禁止创建或维持 Codex Goal，纯等待阶段不得使用 Goal automatic continuation。启动后只向用户简短说明目标、唤醒条件和 120 秒静默轮询，然后由一个长生命周期 Shell command 在同一进程内部完成全部 sleep、查询和条件判断。未命中时该 command 不输出、不结束，也不创建新的 Goal turn。不要把 watcher 放入会周期性恢复模型的 Goal 或循环 Agent 调用中。

当前 Codex turn 只等待这个 execution session。若宿主 API 对一次 wait 调用设有技术上限，仍须附着同一个未退出的 Shell command，且不得借此查询、推理、汇报或创建 Goal；实现应尽量使用宿主允许的最长阻塞等待。Shell 内部的 120 秒检查绝不触发模型判断。等待开销受宿主能力限制，不能承诺跨任意宿主的绝对零 Token。

正常轮询期间禁止输出“还在等待”、完整对象或周期性状态。用户发出“停止观察 / 取消 watch / 不用继续盯了”时，立即向所保存的 execution session 发送中断并确认 watcher 已退出。

## 唤醒、处理与生命周期

Watcher 只在需要行动时退出，输出类似：

```text
ACTION_REQUIRED
target=RI-00497003
role=inspector
reason=PENDING_REVIEW
stage=2
```

命中条件时只输出一次事件并结束 Shell command，使同一个 Codex turn 恢复。收到事件后，不使用启动前缓存作决定。重新读取最新 Issue、当前 Stage、最新 Activity，以及本次动作真正需要的 Review Result、Evidence、Diff、Tests 和代码，再按当前角色流程处理。

一次 watcher 只观察本次明确目标，不扩大到所有 Issue，不把“Stage 2”扩大到后续 Stage。处理完本次事件后默认结束 Watch，不自动重启。只有用户最初明确要求“持续到整个任务结束”时，`continuation=until-target-terminal` 才允许在每次处理完成后，用同一目标和下一明确条件重新校验并启动一个新的一次性 watcher；用户再次明确要求也可重新启动。

用户一次明确指定多个 Issue 时，可把已校验的 Specification 数组交给 `scripts/task_watcher.py --spec-file ...`。它由一个长生命周期 Shell 进程轮询全部目标，未命中时完全静默，只输出第一个需要动作的 Issue；不得为每个 Issue 创建 Goal 或周期性 LLM Turn。一个 Issue 的命中不会自动扩大或续订其他 Issue 的 Watch 生命周期。
