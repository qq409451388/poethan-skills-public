# Code Inspector Webtool

本机 Human 工作台，使用“待我处理 / 检查任务 / 候选问题”三个工作入口，并保持“检查任务 → 任务详情 → Issue 详情”的领域层级。它不负责创建扫描结果；任务、正式 Issue 与候选仍由已开启代码检查模式的 Agent 通过领域命令创建。

## 数据边界

- 页面可以直接读取 SQLite，作为稳定的只读展示层。
- 页面不会直接写 SQLite，也不保留一份状态机或权限规则。
- 每一项写操作都调用 `~/.agent-review/bin/review-db.py --agent human ...`；状态校验、追加活动与审计日志完全由同一领域工具处理。

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
- Issue 详情：展示设计、实现与审核阶段、结构化证据、当前轮实现和协作记录；协作记录默认按最新优先汇总全部内容，同时保留讨论与处理历史筛选，设计、Stage、实现等正式协作提交会同时投影到讨论和历史但只落库一次。Human 可处理最终边界/安全确认，用专用 `human-confirmation-resolve` 恢复设计或实现流程，但不能借此直接 `CONFIRMED`。所有写操作仍走 human 领域命令。
- 候选问题：默认显示 `SUBMITTED` / `UNDER_REVIEW`，支持任务和状态筛选；接受与拒绝都要求填写审核结论，且接受不会自动创建正式 Issue。

弹窗支持遮罩、关闭按钮和 ESC 关闭，Tab 切换时不会丢失当前页面上下文。活动内容继续支持换行、列表、行内代码和 fenced Markdown 代码块。

活动历史会兼容 Agent 误把整段换行保存为字面 `\n` 的旧内容；仅当正文完全没有真实换行时才还原，避免破坏代码中的有意转义。Human 的任务状态表单允许设置任一合法状态，包括重新打开终态任务，写入仍由领域命令完成。

界面文案均为中文；数据库中的稳定枚举值只在内部和链接键中使用。

## 环境变量

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `WEBTOOL_PORT` | 监听端口 | `5050` |
| `WEBTOOL_DEBUG` | 打开 Flask 调试模式 | 未设置 |
| `AGENT_REVIEW_HOME` | Code Inspector 本地目录 | `~/.agent-review` |
| `AGENT_REVIEW_DB` | 只读数据库路径覆盖 | 从 `runtime.json` 读取 |

## 开发结构

```text
apps/code-inspector-webtool/
├── app.py        # 路由、任务/问题只读查询
├── commands.py   # human 领域命令调用适配
├── db.py         # SQLite 只读连接与 JSON 解析
├── static/       # 工作台样式和无业务规则的原生交互
└── templates/    # 页面模板及少量复用组件
```

`review-web`（Windows 为 `review-web.cmd`）是安装时生成的本地启动器；应用源代码仍在本仓库。拉取本仓库的新版本后，下一次启动会直接使用新代码，无需重复安装。
