# FastMode：Human Coordinated Fast Review

仅在用户明确输入以下一级命令时进入 FastMode：

```text
$code-inspector fastmode RI-XXX [RI-YYY ...]
```

`/code-inspector fastmode ...` 完全等价。不要把 `fastmode` 当作 `start insp` 的参数，也不要从自然语言猜测进入此模式。

## 启动与 Session Scope

1. 至少要求一个 Issue Key；没有时明确提示“FastMode 至少需要一个 Issue Key”，不激活。
2. 每个 Key 必须匹配 `RI-[A-Z0-9][A-Z0-9-]*`；格式错误时明确失败。
3. 按输入顺序去重，并逐个执行固定首次读取：

   ```bash
   <fixed_tool> issue-context-get --issue-key <issue_key>
   ```

   `issue-context-get` 使用 `--issue-key`，禁止使用 `--issue-id`。该读取同时验证 Issue 真实存在；任一 Issue 不存在时明确报告 `<issue_key> 不存在`，不得替换成其他 Issue。
4. 启动成功后在 Session 内锁定：`session_role=inspector`、`workflow_mode=FASTMODE`、`issue_scope=[去重后的 Issue Key]`、`developer_agent_required=false`、`human_coordinated=true`。
5. Scope 只存在于当前 Session 内存，不新增数据库字段。后续“重新检查”只能针对当前 Scope；要换一批 Issue，先 `$code-inspector exit`，再重新启动 FastMode。

## 审查行为

- 按启动命令中的顺序串行处理，每个 Issue 单独读取、检查、测试、记录和返回结论，不合并审核结果。
- Human 的显式 FastMode 请求就是工作来源；即使 `pending_action=null`，也继续当前审查。不要改变 Supervisor 或 Projection 的 `pending_action` 语义。
- Developer Session、Developer Thread、Developer Event、`IMPLEMENTATION_SUBMITTED` 和 `implementation-submit` 都不是前置条件；不得伪造这些记录。
- 不启动 Watch、Multi-Thread、Developer Agent 或 Runtime 调度，不创建 Developer Activity。
- 只检查当前代码、Git Diff/Commit、测试、Issue 原始问题、历史讨论和历史验证。Scope 外的新问题沿用 Candidate 机制，不得加入本次 FastMode。
- Inspector 只负责检查与记录，不修改业务代码，不替 Developer 提交实现，不自动设置 `CONFIRMED`、`CANCELLED` 或其他终态。Human 负责开发沟通和最终状态；已有 `CONFIRMED` 安全 Gate 不得绕过。

## 记录结果

需要正文时仍可使用 `issue-get`、`activity-get`、`discussion-get`、`stage-history-get`；可以执行测试和静态检查，并用 `discussion-append` 补充讨论。

每个 Issue 的验证证据和结论分开记录，并在 `--metadata` 中只标记来源：

```bash
<fixed_tool> activity-append --issue-key <issue_key> --activity-type VERIFICATION_EVIDENCE_ADDED --content <evidence_summary> --metadata '{"workflow_mode":"FASTMODE"}'
<fixed_tool> activity-append --issue-key <issue_key> --activity-type VERIFICATION_PASSED --content <result_summary> --result-status PASS --metadata '{"workflow_mode":"FASTMODE"}'
```

失败时将最后一条替换为 `VERIFICATION_FAILED` 和 `--result-status FAIL`，正文明确列出失败证据与必须修改项。FastMode 的 `VERIFICATION_FAILED` 不触发 Developer Runtime Event。

PASS 后只告知“结果已记录，等待你决定是否关闭 Issue”；FAIL 后提供可直接转发给开发的必须修改项。全部 Issue 完成后可以汇总，但每个 Issue 的结论必须保持独立。
