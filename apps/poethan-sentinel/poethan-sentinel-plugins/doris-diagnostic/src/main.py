import sys
from datetime import datetime, timezone
from src.util import section
from src.collectors import host, process, systemd, resources, doris_sql, threads, perf, logs, flink
from src.checks import process_consistency

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "standard"
    section("META", f"plugin_id=doris-diagnostic\nplugin_version=0.3.0\nmode={mode}\nstarted_at={datetime.now(timezone.utc).isoformat()}")
    section("HOST", host.collect())
    process_text = process.collect(); section("DORIS_PROCESS", process_text); section("CHECKS", process_consistency.evaluate(process_text))
    if mode == "quick": return
    section("SYSTEMD_HISTORY", systemd.collect())
    section("SYSTEM_RESOURCES", resources.collect())
    section("DORIS_SQL", doris_sql.collect())
    thread_text, hot, be_cpu = threads.collect(); section("HOT_THREADS", thread_text)
    section("DORIS_LOG_FINDINGS", logs.collect())
    section("FLINK", flink.collect())
    if mode == "deep": section("PERF", perf.collect(hot, be_cpu))

if __name__ == "__main__": main()
