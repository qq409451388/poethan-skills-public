from app import markdown


def test_renders_headings_paragraphs_and_inline_markup() -> None:
    html = markdown.render("## 结论\n\n系统**负载**偏高，参考 `load1`。")
    assert "<h2>结论</h2>" in html
    assert "<strong>负载</strong>" in html
    assert "<code>load1</code>" in html
    assert html.endswith("</p>")


def test_renders_lists_quotes_and_tables() -> None:
    html = markdown.render("- 第一项\n- 第二项\n\n> 引用\n\n| 名称 | 值 |\n| --- | --- |\n| load1 | 1.42 |")
    assert "<ul><li>第一项</li><li>第二项</li></ul>" in html
    assert "<blockquote><p>引用</p></blockquote>" in html
    assert "<th>名称</th><th>值</th>" in html
    assert "<td>load1</td><td>1.42</td>" in html


def test_fenced_code_block_is_escaped_and_not_formatted() -> None:
    html = markdown.render("```bash\nrm -rf <path> && echo `date`\n```")
    assert "<pre><code class=\"language-bash\">" in html
    assert "&lt;path&gt;" in html
    assert "&amp;&amp;" in html
    assert "<code>date</code>" not in html


def test_raw_html_is_escaped_instead_of_executed() -> None:
    html = markdown.render("<script>alert('x')</script>\n\n<img src=x onerror=alert(1)>")
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html


def test_unsafe_link_schemes_are_downgraded_to_text() -> None:
    assert "<a " not in markdown.render("[点我](javascript:alert(1))")
    assert "点我" in markdown.render("[点我](javascript:alert(1))")
    safe = markdown.render("[文档](https://example.com/a?b=1&c=2)")
    assert 'href="https://example.com/a?b=1&amp;c=2"' in safe


def test_bold_inside_inline_code_is_left_alone() -> None:
    html = markdown.render("执行 `a**b**c` 命令")
    assert "<code>a**b**c</code>" in html
    assert "<strong>" not in html


def test_underscores_in_identifiers_and_paths_are_not_emphasis() -> None:
    """诊断输出里全是 snake_case；下划线跨词配对会把路径吃成斜体。"""
    html = markdown.render("扩容 /mnt/storage_archive，影响 /mnt/mysql_data2 上的读写")
    assert "<em>" not in html
    assert "/mnt/storage_archive" in html
    assert "/mnt/mysql_data2" in html


def test_dunder_names_stay_literal() -> None:
    """__pycache__ / __init__ 这类名字不能被当成加粗。"""
    html = markdown.render("清理 __pycache__ 与 app/__init__.py")
    assert "<strong>" not in html
    assert "__pycache__" in html
    assert "__init__" in html


def test_chinese_adjacent_underscore_emphasis_still_works() -> None:
    assert "<em>重要</em>" in markdown.render("这一项是_重要_的")
    assert "<em>note</em>" in markdown.render("这是 _note_ 文本")


def test_underscore_and_star_lists_render_as_lists() -> None:
    assert "<ul>" in markdown.render("* 甲\n* 乙")
    assert "<ol><li>甲</li><li>乙</li></ol>" in markdown.render("1. 甲\n2. 乙")


def test_horizontal_rule_and_empty_input() -> None:
    assert "<hr/>" in markdown.render("---")
    assert markdown.render("") == ""
