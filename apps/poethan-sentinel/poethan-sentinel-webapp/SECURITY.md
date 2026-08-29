# 安全模型

Poethan Sentinel WebApp 是只监听 `127.0.0.1` 的本地工具，不提供公网控制器模式。浏览器负责界面；SSH、凭据、插件验证和 AI 请求均由本机 Controller 处理。

## 凭据与连接

- 服务器密码、插件密码字段和 AI Key 使用系统 Keychain；JSON 配置不保存明文。
- SSH 主机密钥默认严格验证。首次连接必须由用户确认指纹；指纹变化时阻止连接。
- Controller 要求 HttpOnly 本机会话 Cookie；写操作额外要求请求标记，并限制受信 Origin。
- 日志、SSE 和报告写入前会对本次使用的敏感值做替换脱敏。

## 插件信任

- 正式模式只执行已由受信 Ed25519 公钥验证的插件。
- `plugin.lock.json` 覆盖包内所有文件；导入、上传和远端解包均验证 SHA-256。
- 同一插件内容安装到 `/opt/poethan-sentinel/plugins/<id>/<version>/<lockDigest>/`，不覆盖已有摘要。
- 未签名包仅能在开发者模式加载，并始终标记为不受信。
- 签名证明来源与完整性，不证明脚本天然安全。安装前仍应检查插件声明的 sudo、perf、网络、日志和数据库权限。

## 报告模板

插件 HTML 报告只在通过 Schema 校验后生成，并使用 CSP sandbox 打开；禁止网络、表单、弹窗和同源访问。AI 失败不会覆盖本地原始输出或确定性结论。

## 发布密钥

私钥不得提交到仓库或打包进应用。使用 `scripts/plugin_sign.py keygen` 在受保护环境创建发布密钥，将公钥写入 `contracts/trusted-publishers.json`，私钥放入离线介质或受保护的 CI Secret。正式发布前必须替换仓库当前的 Beta 开发公钥并重新签署三个内置插件。

发现安全问题时，请先通过仓库维护者的私密联系方式报告，不要在公开 Issue 中附带服务器地址、密钥、诊断输出或业务数据。
