from app.reports import parse_findings


def test_parses_check_and_hot_thread_findings() -> None:
    output = """check_id=HOST-001
status=failed
value=2.0
threshold=1.0

===== SECTION: HOT_THREADS =====
cpu_avg=98.4
"""
    findings = parse_findings(output, 0)
    assert any(item.title == "系统负载超过配置阈值" for item in findings)
    assert any(item.title == "发现持续热线程" for item in findings)


def test_success_when_no_rule_fires() -> None:
    findings = parse_findings("status=passed", 0)
    assert len(findings) == 1
    assert findings[0].severity == "success"
