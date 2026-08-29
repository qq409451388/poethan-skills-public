# 插件开发与签名

插件是一个独立目录，根目录必须有 `plugin.yaml`。配置清单决定诊断页的运行模式、动态表单、能力声明，以及可选的 JSON Schema 和 HTML 报告模板。

最小结构：

```text
my-diagnostic/
├── plugin.yaml
├── run.sh
└── main.py
```

配置字段支持 `text`、`path`、`integer`、`url`、`password`、`boolean` 和 `choice`。密码字段由 Controller 存入 Keychain；脚本通过 `POETHAN_CONFIG_FILE` 指向的环境文件读取值。脚本只向标准输出写结构化事实，推荐格式：

```text
===== SECTION: HOST =====
hostname=server-1

===== SECTION: CHECKS =====
check_id=HOST-001
status=failed
value=1.42
threshold=1.0
```

Controller 会把远端输出重定向到本次运行的 `/tmp/poethan-sentinel-<run-id>/result.txt`，下载后清理。插件目录按 ID、版本和 lock 摘要长期缓存到服务器，仅内容发生变化时同步。

## 自定义报告

在 `plugin.yaml` 声明：

```yaml
report:
  schema: report/report-schema.json
  template: report/report-template.html
```

模板必须包含 `__REPORT_JSON__` 和 `__REPORT_SCHEMA__` 两个 JavaScript 值占位符。Controller 先用 Schema 验证报告投影，再替换占位符，并在 CSP sandbox 中展示模板。

## 本地调试与签名

开发阶段可在设置中开启“开发者模式”，导入未签名目录。发布时使用 Ed25519：

```bash
controller/.venv/bin/python scripts/plugin_sign.py keygen /secure/release.pem /tmp/release-public.txt
controller/.venv/bin/python scripts/plugin_sign.py sign /path/to/my-diagnostic /secure/release.pem
controller/.venv/bin/python scripts/plugin_sign.py verify /path/to/my-diagnostic /tmp/release-public.txt
```

把发布者公钥和允许签署的插件 ID 范围加入 `contracts/trusted-publishers.json`。修改插件任意文件后必须提升版本并重新签名；不要复用同一个版本替换内容。
