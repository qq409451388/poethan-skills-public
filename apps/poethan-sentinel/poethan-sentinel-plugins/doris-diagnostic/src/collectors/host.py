import os
from src.util import command, env

def collect():
    commands = ["date -Is", "hostname", "uptime", "uname -a", "nproc", "free -h", "df -h", f"readlink -f {env('DORIS_HOME', '/opt/doris/current')}"]
    rows = []
    for item in commands:
        code, output = command(item, shell=True)
        rows.append(f"$ {item}\nexit={code}\n{output}")
    return "\n".join(rows)
