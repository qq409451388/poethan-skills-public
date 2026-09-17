"""自带的 Markdown 渲染器。

AI 在插件没有 HTML 报告模板时返回 Markdown，报告页需要自己把它渲染成 HTML。
这里刻意不引入第三方依赖：先对整个文档做 HTML 转义，再只识别白名单语法，
因此模型输出里的任何标签都会被当作纯文本，不存在脚本注入面。

支持的语法是诊断报告实际会用到的子集：标题、段落、无序/有序列表、引用、
围栏代码块、表格、分隔线，以及行内的粗体、斜体、行内代码和链接（仅 http/https）。
嵌套列表和原始 HTML 不在支持范围内，会退化成普通文本。
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse


_FENCE = re.compile(r"^```(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HORIZONTAL_RULE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")
_UNORDERED_ITEM = re.compile(r"^\s{0,3}[-*+]\s+(.*)$")
_ORDERED_ITEM = re.compile(r"^\s{0,3}\d{1,9}[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_DIVIDER = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_INLINE_CODE = re.compile(r"`([^`]+)`")
# 只认 **加粗**：__ 在诊断输出里几乎总是标识符（__pycache__、__init__），
# 按 CommonMark 渲染成加粗反而会吃掉路径，这里当作普通文本。
_BOLD = re.compile(r"\*\*(.+?)\*\*")
# CommonMark 规定 _ 不能词内配对，否则 /mnt/mysql_data2 会被跨词吃成斜体。
# 只对 ASCII 字母数字做限制，中文旁边的 _强调_ 仍然可用。
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<![0-9A-Za-z_])_([^_\n]+?)_(?![0-9A-Za-z_])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

# 行内代码先寄存成占位符，避免其中的 * _ 等符号被后续规则改写。
_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")
_SAFE_LINK_SCHEMES = {"http", "https"}
_HEADING_LEVEL_MAX = 6


def _link(match: re.Match[str]) -> str:
    label, target = match.group(1), html.unescape(match.group(2))
    if urlparse(target).scheme not in _SAFE_LINK_SCHEMES:
        return label
    href = html.escape(target, quote=True)
    return f'<a href="{href}" target="_blank" rel="noreferrer noopener">{label}</a>'


def _inline(raw: str) -> str:
    """先转义原始文本，再在其上应用行内语法。"""
    text = html.escape(raw, quote=False)
    codes: list[str] = []

    def stash(match: re.Match[str]) -> str:
        codes.append(match.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = _INLINE_CODE.sub(stash, text)
    text = _LINK.sub(_link, text)
    text = _BOLD.sub(lambda match: f"<strong>{match.group(1)}</strong>", text)
    text = _ITALIC.sub(lambda match: f"<em>{match.group(1) or match.group(2)}</em>", text)
    text = _PLACEHOLDER.sub(lambda match: f"<code>{codes[int(match.group(1))]}</code>", text)
    return text


def _split_row(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def _is_table_start(lines: list[str], index: int) -> bool:
    if "|" not in lines[index] or index + 1 >= len(lines):
        return False
    return bool(_TABLE_DIVIDER.match(lines[index + 1]))


def _starts_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    if _FENCE.match(line.strip()) or _HEADING.match(line) or _HORIZONTAL_RULE.match(line):
        return True
    if _QUOTE.match(line) or _UNORDERED_ITEM.match(line) or _ORDERED_ITEM.match(line):
        return True
    return _is_table_start(lines, index)


def _render_table(lines: list[str], index: int) -> tuple[str, int]:
    header = _split_row(lines[index])
    index += 2
    rows: list[list[str]] = []
    while index < len(lines) and lines[index].strip() and "|" in lines[index]:
        rows.append(_split_row(lines[index]))
        index += 1
    head = "".join(f"<th>{_inline(cell)}</th>" for cell in header)
    body = "".join("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>", index


def _render_list(lines: list[str], index: int, ordered: bool) -> tuple[str, int]:
    pattern = _ORDERED_ITEM if ordered else _UNORDERED_ITEM
    items: list[str] = []
    while index < len(lines):
        match = pattern.match(lines[index])
        if not match:
            break
        items.append(_inline(match.group(1)))
        index += 1
    tag = "ol" if ordered else "ul"
    body = "".join(f"<li>{item}</li>" for item in items)
    return f"<{tag}>{body}</{tag}>", index


def render(source: str) -> str:
    """把 Markdown 转成安全的 HTML 片段。"""
    text = (source or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        fence = _FENCE.match(line.strip())
        if fence:
            language = fence.group(1).strip()
            index += 1
            body: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                body.append(lines[index])
                index += 1
            index += 1  # 关闭围栏；缺失时按文末结束处理
            attributes = f' class="language-{html.escape(language, quote=True)}"' if language else ""
            blocks.append(f"<pre><code{attributes}>{html.escape(chr(10).join(body))}</code></pre>")
            continue

        heading = _HEADING.match(line)
        if heading:
            level = min(len(heading.group(1)), _HEADING_LEVEL_MAX)
            blocks.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if _HORIZONTAL_RULE.match(line):
            blocks.append("<hr/>")
            index += 1
            continue

        if _QUOTE.match(line):
            quoted: list[str] = []
            while index < len(lines) and _QUOTE.match(lines[index]):
                quoted.append(_QUOTE.match(lines[index]).group(1))
                index += 1
            blocks.append(f"<blockquote>{render(chr(10).join(quoted))}</blockquote>")
            continue

        if _UNORDERED_ITEM.match(line) or _ORDERED_ITEM.match(line):
            block, index = _render_list(lines, index, ordered=bool(_ORDERED_ITEM.match(line)))
            blocks.append(block)
            continue

        if _is_table_start(lines, index):
            block, index = _render_table(lines, index)
            blocks.append(block)
            continue

        paragraph: list[str] = []
        while index < len(lines) and lines[index].strip() and not _starts_block(lines, index):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append(f"<p>{_inline(' '.join(paragraph))}</p>")

    return "".join(blocks)
