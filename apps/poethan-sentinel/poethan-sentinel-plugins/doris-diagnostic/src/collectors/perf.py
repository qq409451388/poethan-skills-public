import os
from src.util import command

def collect(hot_threads, be_cpu):
    persistent = [item for item in hot_threads if len(item[3]) == 3 and min(item[3]) > 50]
    if be_cpu <= 200 or not persistent: return f"status=skipped\nbe_cpu={be_cpu:.1f}\nreason=trigger_not_met"
    result_dir = os.environ.get("POETHAN_RESULT_DIR", "."); rows = [f"be_cpu={be_cpu:.1f}"]
    for _, tid, name, _ in persistent[:3]:
        data = os.path.join(result_dir, "artifacts", f"perf-{tid}.data"); report = os.path.join(result_dir, "artifacts", f"perf-{tid}.txt")
        code, output = command(["perf", "record", "-F", "99", "-g", "-o", data, "-t", tid, "--", "sleep", "10"], timeout=20)
        if code == 0:
            report_code, text = command(["perf", "report", "--stdio", "--children", "-i", data], timeout=30)
            with open(report, "w", encoding="utf-8") as handle: handle.write(text)
            rows += [f"tid={tid}", f"thread={name}", "sample_seconds=10", f"report_exit={report_code}", f"artifact={os.path.basename(report)}", "top_stack:", *text.splitlines()[:80], ""]
        else: rows += [f"tid={tid}", f"thread={name}", f"perf_exit={code}", output, ""]
    return "\n".join(rows)
