# Code Inspector Webtool

本机 Human 工作台，使用“待我处理 / 检查任务 / 候选问题 / AI 运行情况”四个入口，并保持“检查任务 → 任务详情 → Issue 详情”的领域层级。它不负责创建扫描结果；任务、正式 Issue 与候选仍由已开启代码检查模式的 Agent 通过领域命令创建。

## 数据边界

- 页面可以直接读取 SQLite，作为稳定的只读展示层。
- 页面不会直接写 SQLite，也不保留一份状态机或权限规则。
- 业务写操作调用 `~/.agent-review/bin/review-db.py --agent human ...`；状态校验、追加活动与审计日志由同一领域工具处理。
- Runtime 的 Retry/Reconcile/Pause 调用 `~/.agent-review/bin/code-inspector-supervisor.py`，不允许页面直接 UPDATE Runtime 表。

因此，数据库结构和规则只收敛在 Code Inspector 的迁移与运行工具中，Webtool 不会因为规则调整而复制出第二套实现。

## 安装与启动

macOS / Linux 先完成 Code Inspector 的基础安装，再安装可选启动器：

```bash
python3 scripts/code-inspector-installer/install.py install
python3 scripts/code-inspector-installer/install.py install-webtool
```

Windows 在 PowerShell 中执行：

```powershell
python scripts/code-inspector-installer/install.py install
python scripts/code-inspector-installer/install.py install-webtool
```

安装 Flask（仅第一次需要）：

```bash
python3 -m pip install -r apps/code-inspector-webtool/requirements.txt
```

macOS / Linux 启动：

```bash
~/.agent-review/bin/review-web
```

Windows 启动：

```powershell
& "$HOME\.agent-review\bin\review-web.cmd"
```

浏览器访问 `http://127.0.0.1:5050/`。默认仅监听本机。也可以直接运行：

```bash
python3 apps/code-inspector-webtool/app.py
```

## 页面结构

- 待我处理：首页顶部先显示最多 6 个最近活跃任务，活跃时间综合 Task、Issue、Activity 和 Candidate 的最近变化；下方按待审核实现、需要人工确认、受阻和候选待审核聚合 Human 当前工作。`HUMAN_CONFIRMATION_REQUIRED` 会突出显示 Inspector 整理的原因、问题、选项、推荐和关键风险；普通 `INSPECTOR_CONFIRMATION_REQUIRED` 仍由 Agent 自主处理，不计入 Human 待确认。
- 检查任务：默认显示活动任务，可按项目、状态和 `REVIEW` / `CONTINUOUS` 类型筛选并选择显示已关闭任务；整行进入任务详情。
- 任务详情：展示任务信息、版本历史、统计指标和可组合筛选的问题列表；任务编辑集中在弹窗中。
- Issue 详情：`difficulty` 面板区分「已选定的执行者」（assignment）与「候选执行配置」（推荐，实时计算）。assignment 失效时显示“当前执行配置已失效，请重新选择。”并给出原因；指定执行者的下拉只列出 `enabled` 且 `level >= difficulty` 的合法候选。Routing 配置变化不会自动改选执行者，也不会删除历史分配。
- Issue 详情：展示设计、实现与审核阶段、结构化证据、当前轮实现和协作记录；协作记录默认按最新优先汇总全部内容，同时保留讨论与处理历史筛选，设计、Stage、实现等正式协作提交会同时投影到讨论和历史但只落库一次。Human 可处理最终边界/安全确认，用专用 `human-confirmation-resolve` 恢复设计或实现流程，但不能借此直接 `CONFIRMED`。所有写操作仍走 human 领域命令。
- AI 运行情况：`/runtime` 默认展示今日 Token、处理 Issue、模型唤醒、过期事件拦截、需要关注项和 Issue 消耗排行；异常文案使用 Human 可理解的中文。Thread、Event、Lease、Revision、Turn Metrics 及 Retry/Reconcile/Pause 完整保留在默认折叠的“高级诊断”中，管理操作只经过 Runtime CLI 并写审计。Issue 详情同步展示该 Issue 的 AI 消耗、工具读取和折叠的最近 Turn。
- 候选问题：默认显示 `SUBMITTED` / `UNDER_REVIEW`，支持任务和状态筛选；接受与拒绝都要求填写审核结论，且接受不会自动创建正式 Issue。
- 模型路由配置：查看本机 `~/.agent-review/config/agent-routing.yml` 的状态（未配置 / 配置错误 / 已启用）、校验错误和 Dev 执行配置列表。按 Agent 分组，一个 Agent 可配置多个 Model；Model 与 Reasoning 选项来自 Skill 内置的 `agent-capabilities.yml`，Reasoning 随所选 Model 联动；等级用离散滑杆（1..5）。支持新增、删除、保存、校验和 YAML 原始编辑。这是本功能唯一绕过 `review-db.py` 的写操作，因为它修改的是本机配置文件而不是 Review DB：后端完整校验后写临时文件、原子替换正式文件，再 reload 运行时内存快照，保存成功即时生效，无需重启。校验失败直接返回且不修改现有文件；reload 失败时继续沿用上一份有效运行时配置。从本机 Agent 初始化默认与现有配置合并（保护人工调整），整体替换需显式选择。页面与 Runtime 共用同一份路径解析与校验实现，并且模块优先取安装目录、缺失时回退源码 checkout。

弹窗支持遮罩、关闭按钮和 ESC 关闭，Tab 切换时不会丢失当前页面上下文。活动内容继续支持换行、列表、行内代码和 fenced Markdown 代码块。

活动历史会兼容 Agent 误把整段换行保存为字面 `\n` 的旧内容；仅当正文完全没有真实换行时才还原，避免破坏代码中的有意转义。Human 的任务状态表单允许设置任一合法状态，包括重新打开终态任务，写入仍由领域命令完成。

界面文案均为中文；数据库中的稳定枚举值只在内部和链接键中使用。

## 环境变量

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `WEBTOOL_PORT` | 监听端口 | `5050` |
| `WEBTOOL_DEBUG` | 仅 `1/true/yes/on` 打开 Flask 调试模式 | 未设置 |
| `WEBTOOL_SECRET_KEY` | session/CSRF 签名密钥；生产长期运行建议设置稳定随机值 | 每次进程启动随机生成 |
| `AGENT_REVIEW_HOME` | Code Inspector 本地目录 | `~/.agent-review` |
| `AGENT_REVIEW_DB` | 只读数据库路径覆盖 | 从 `runtime.json` 读取 |

## 开发结构

```text
apps/code-inspector-webtool/
├── app.py        # 路由、任务/问题只读查询
├── commands.py   # human 领域命令与 Runtime 管理 CLI 调用适配
├── db.py         # SQLite 只读连接与 JSON 解析
├── routing.py    # 模型路由配置的读取、校验与原子保存适配
├── static/       # 工作台样式和无业务规则的原生交互
└── templates/    # 页面模板及少量复用组件
```

`review-web`（Windows 为 `review-web.cmd`）是安装时生成的本地启动器；应用源代码仍在本仓库。拉取本仓库的新版本后，下一次启动会直接使用新代码，无需重复安装。

所有 POST/PUT/PATCH/DELETE 请求都验证 session CSRF token；即使服务只监听 `127.0.0.1`，来自恶意网页的 localhost 表单请求也会被拒绝。
