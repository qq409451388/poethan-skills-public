# Poethan Sentinel

Poethan Sentinel 是一套可扩展的远程服务器诊断工具。客户端负责编排服务器、插件、执行结果、AI 分析和报告；诊断逻辑由独立的 Python/Bash 插件提供。

## 项目结构

```text
poethan-sentinel/
├── poethan-sentinel-macapp/   原生 macOS 工作流客户端
├── poethan-sentinel-plugins/  可独立安装和升级的诊断插件
└── poethan-sentinel-webapp/   React + FastAPI 本地 Web 客户端（当前优先）
```

三个子项目保持独立边界：客户端不内置诊断插件；插件通过 `plugin.yaml` 描述执行入口、配置表单和报告资源；Web 和 macOS 客户端复用同一插件协议和报告格式。

## 启动 WebApp

```bash
cd poethan-sentinel-webapp
./scripts/install.sh
./scripts/start.sh
```

打开 <http://127.0.0.1:8765>。演示服务器可以直接走完插件选择、配置、执行和报告流程。

## 本地启动 macOS 客户端

```bash
cd poethan-sentinel-macapp
./scripts/build-app.sh debug
open '.build/Poethan Sentinel.app'
```

开发时可在 App 的“设置 → 插件目录”中选择 `../poethan-sentinel-plugins`，或通过“管理检查脚本”逐个导入插件。

## 发布边界

- `poethan-sentinel-macapp`：发布 `.app` 或安装包。
- `poethan-sentinel-plugins`：按插件 ID 和语义化版本独立发布。
- `poethan-sentinel-webapp`：Beta 1 主客户端，流程稳定后再迁移 SwiftUI。

版本记录、安全说明和安装文档已放在 WebApp 目录。公开分享前仍需由仓库所有者选择开源许可证并换用正式离线发布密钥。

## 后续计划

- [Beta 1 后续计划](BETA1_PLAN.md)：WebApp 工程化、真实诊断闭环、插件签名与公开发布门槛。
