# Poethan Sentinel WebApp

本地优先的远程服务器诊断工作台。React 页面维护服务器、插件、运行配置和报告；FastAPI Controller 负责 Keychain、SSH、插件可信校验、远端执行与 AI 请求。Controller 只监听 `127.0.0.1`。

## 5 分钟开始

```bash
./scripts/install.sh
./scripts/start.sh
```

浏览器会打开 <http://127.0.0.1:8765>。首次体验直接选择“演示服务器”，无需真实服务器或 AI Key。停止服务执行 `./scripts/stop.sh`；更新源码后重新执行 `./scripts/install.sh`。

## 已实现

- SSH 别名、密钥和密码登录，严格主机指纹确认。
- 外部插件目录、目录导入、动态表单和明确的校验失败原因。
- 首次启动把随发行包提供的签名插件安装到用户插件目录，后续按版本独立维护。
- Ed25519 发布者签名、全文件 SHA-256 和上传后远端复核。
- 单插件四步工作流、远端版本缓存、临时结果下载和 SSE 进度。
- 确定性结论、原始输出、AI 增强、历史记录和沙箱 HTML 报告。
- 演示服务器可完整走完 Doris、主机性能和网络诊断流程。

## 开发

```bash
./scripts/dev.sh
```

开发页为 <http://127.0.0.1:4173>，API 文档为 <http://127.0.0.1:8765/api/docs>。完整自检：

```bash
./scripts/check.sh
```

详细协议见 [插件开发与签名](PLUGIN_DEVELOPMENT.md) 和 [安全模型](SECURITY.md)。静态原型保留在 `prototype/`，作为 Web 与未来 SwiftUI 迁移的交互基线。
