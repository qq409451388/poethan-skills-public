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

模板必须包含 `__REPORT_JSON__` 占位符。Controller 先用 Schema 验证报告数据，再替换占位符，并在 CSP sandbox 中展示模板。

```html
<script>
const report = __REPORT_JSON__;   // 报告数据，已按你的 Schema 校验
const schema = __REPORT_SCHEMA__; // 可选：Schema 原文，不需要就不要声明
const ai = __REPORT_AI__;         // 可选：AI 分析结果，没有结果时为 null
</script>
```

`__REPORT_SCHEMA__` 是可选的：模板不需要 Schema 原文时不必声明一个用不到的变量。

### 报告数据的两种来源

`__REPORT_JSON__` 默认是应用统一的报告投影（`schemaVersion`/`server`/`summary`/`findings`/`outputs` 等），适合数据不复杂的插件。

如果模板需要自己的数据结构，插件可以在事实流里输出一个 `REPORT_DATA` 区段，值为一行 JSON：

```text
===== SECTION: REPORT_DATA =====
{"h":"prod-1","ts":"2026-09-17 13:10","d":[["/",536870912000,365072220160,"ext4"]],"dirs":[["/var",38654705664]],"files":[]}
```

Controller 会优先使用它，并按你声明的 Schema 校验；区段缺失或不是合法 JSON 时自动退回统一投影，不会让报告页打不开。这样模板能完全按自己的形状拿数据，百分比、可用空间之类的派生值由页面自己算。

`__REPORT_AI__` 的渲染规则：

- 模板声明了该占位符 → 由模板自己决定怎么展示 AI。
- 模板没声明，但本次运行有 AI 结果 → Controller 在 `</body>` 前追加一个默认 AI 区块。

AI 是每次运行时可选开启的；不开启时报告页只有确定性内容，不受模板影响。

## AI 分析的两种输出格式

AI 分析按插件是否声明 `report` 走两套格式规则，提示词和落盘结构都在 Controller 里维护（`controller/app/ai.py`）：

| 插件情况 | AI 输出 | 渲染方式 |
| --- | --- | --- |
| 声明了 `report.template` | JSON | Controller 按 `contracts/ai-report.schema.json` 校验，通过 `__REPORT_AI__` 赋值给模板页面 |
| 没有声明模板 | Markdown | 应用内置报告页自己解析 Markdown 并排版 |

JSON 契约（`summary` 与 `findings` 必填，其余可选）：

```json
{
  "summary": "一句话总体结论",
  "rootCause": "最可能的根因，证据不足时写“无法确定”",
  "confidence": "high | medium | low",
  "findings": [
    {"severity": "critical | warning | info | success", "title": "标题", "evidence": "证据", "recommendation": "建议"}
  ],
  "actions": [
    {"priority": 1, "action": "按优先级排序的处理动作", "risk": "low | medium | high"}
  ]
}
```

模型没有按契约返回时不会丢结果：Controller 会把原文降级为 Markdown 展示，并在报告页标注降级原因。因此在模板里读取 `__REPORT_AI__` 必须判空。

### 没有模板时的输出结构

没有 `report` 的插件走 Markdown，Controller 先用确定性 findings 判定情形，再只把对应那一套骨架写进提示词，模型不能自由加章节：

| 情形 | 判据 | 输出 | 字数预算 |
| --- | --- | --- | --- |
| 正常 | 无 critical/warning | 结论 + 摘要 | ≤200 字 |
| 异常 | 有 critical/warning | 结论 + 摘要 + 问题分析 | ≤600 字 |
| 证据不足 | 输出为空或脚本异常退出且无断言 | 结论 + 摘要（缺什么证据、补采什么） | ≤250 字 |

异常档的「问题分析」可以按插件关闭：

```yaml
ai:
  problemAnalysis: false   # 有异常时也只输出结论和摘要（≤250 字）
```

未声明时默认 `problemAnalysis: true`。超出字数预算的内容会被按整行截断并标注。

## 本地调试与签名

开发阶段可在设置中开启“开发者模式”，导入未签名目录。发布时使用 Ed25519：

```bash
controller/.venv/bin/python scripts/plugin_sign.py keygen /secure/release.pem /tmp/release-public.txt
controller/.venv/bin/python scripts/plugin_sign.py sign /path/to/my-diagnostic /secure/release.pem
controller/.venv/bin/python scripts/plugin_sign.py verify /path/to/my-diagnostic /tmp/release-public.txt
```

把发布者公钥和允许签署的插件 ID 范围加入 `contracts/trusted-publishers.json`。修改插件任意文件后必须提升版本并重新签名；不要复用同一个版本替换内容。
