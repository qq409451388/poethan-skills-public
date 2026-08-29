# Poethan Sentinel macOS App

原生 SwiftUI 客户端，负责服务器配置、插件发现与校验、SSH 执行、结果收集、AI 增强分析和 HTML 报告。

诊断插件不打包进 `.app`。默认插件目录为：

```text
~/Library/Application Support/Poethan Sentinel/plugins
```

构建：

```bash
swift test
./scripts/build-app.sh debug
```
