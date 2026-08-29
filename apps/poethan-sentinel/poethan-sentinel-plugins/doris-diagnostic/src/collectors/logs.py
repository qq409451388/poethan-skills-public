import os
from src.util import command, env

def collect():
    home = env("DORIS_HOME", "/opt/doris/current")
    paths = [env("DORIS_FE_LOG", os.path.join(home, "fe/log/fe.log")), env("DORIS_BE_LOG", os.path.join(home, "be/log/be.INFO"))]
    rows = []
    for path in paths:
        _, output = command(["tail", "-n", "500", path], timeout=20)
        findings = [line for line in output.splitlines() if any(word in line.lower() for word in ["error", "warn", "fatal", "no queryable replicas", "already running", "failed", "rocksdb", "tablet", "timeout"])]
        rows += [f"path={path}", *findings[-200:], ""]
    return "\n".join(rows)
