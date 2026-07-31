# Code Inspector Webtool

本机人工工作台，使用固定的“检查任务 → 问题”视图：首页只列检查任务，进入任务后查看和处理它下面的问题。它不负责创建扫描结果；任务与问题仍由已开启代码检查模式的 Agent 通过领域命令创建。

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

- 检查任务：首页；默认仅显示待开始和进行中的任务，可按项目、状态筛选并选择显示已关闭任务。
- 任务详情：任务目标、检查范围、级别、版本历史和问题概览；问题是该页内的唯一工作列表。
- 问题详情：证据、结构化字段、活动历史，以及人工补充、评级、状态变更入口。活动内容支持换行、列表、行内代码和 fenced Markdown 代码块。

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
├── static/
└── templates/
```

`review-web`（Windows 为 `review-web.cmd`）是安装时生成的本地启动器；应用源代码仍在本仓库。拉取本仓库的新版本后，下一次启动会直接使用新代码，无需重复安装。
