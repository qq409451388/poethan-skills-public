import re
from src.util import command, env

KEYWORDS = re.compile(r"Starting|Started|Stopping|Stopped|Failed|already running|dependency|Main process exited|Scheduled restart job|Can't open PID file", re.I)

def collect():
    rows = []
    for label, unit in [("FE", env("DORIS_FE_UNIT", "doris-fe.service")), ("BE", env("DORIS_BE_UNIT", "doris-be.service"))]:
        _, output = command(["journalctl", "-u", unit, "--since", "24 hours ago", "--no-pager"], timeout=30)
        selected = [line for line in output.splitlines() if KEYWORDS.search(line)]
        restart_count = sum("Scheduled restart job" in line or "Starting " in line for line in selected)
        rows += [f"component={label}", f"restart_events_24h={restart_count}", *selected[-120:], ""]
    return "\n".join(rows)
