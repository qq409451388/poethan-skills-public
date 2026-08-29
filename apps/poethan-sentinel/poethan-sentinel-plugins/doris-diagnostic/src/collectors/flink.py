import json
from urllib.request import urlopen
from src.util import env

def _get(url):
    try:
        with urlopen(url, timeout=10) as response: return json.load(response)
    except Exception as exc: return {"collector_error": f"{type(exc).__name__}:{exc}"}

def collect():
    if env("FLINK_ENABLED", "false").lower() != "true": return "status=disabled"
    base = env("FLINK_URL", "http://127.0.0.1:8081").rstrip("/"); overview = _get(base + "/jobs/overview"); rows = []
    for job in overview.get("jobs", []):
        job_id = job.get("jid", ""); state = job.get("state", "unknown")
        rows += [f"job_id={job_id}", f"job_name={job.get('name', '')}", f"job_state={state}"]
        if state == "FAILED": rows += ["exceptions=" + json.dumps(_get(f"{base}/jobs/{job_id}/exceptions"), ensure_ascii=False), "checkpoints=" + json.dumps(_get(f"{base}/jobs/{job_id}/checkpoints"), ensure_ascii=False)]
        rows.append("")
    return "\n".join(rows) if rows else json.dumps(overview, ensure_ascii=False)
