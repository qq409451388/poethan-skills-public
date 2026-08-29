from src.util import command, env

def _show(unit):
    _, output = command(["systemctl", "show", unit, "-p", "MainPID", "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts"])
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

def _pids(pattern):
    _, output = command(["pgrep", "-f", pattern])
    return [p for p in output.split() if p.isdigit()]

def collect():
    result = []
    checks = [("FE", env("DORIS_FE_UNIT", "doris-fe.service"), "org.apache.doris.DorisFE"), ("BE", env("DORIS_BE_UNIT", "doris-be.service"), "(^|/)doris_be( |$)")]
    for name, unit, pattern in checks:
        state = _show(unit); pids = _pids(pattern); main = state.get("MainPID", "0")
        cgroups = []
        for pid in pids:
            _, value = command(["cat", f"/proc/{pid}/cgroup"]); cgroups.append(f"{pid}:{value.replace(chr(10), '|')}")
        managed = main != "0" and pids == [main]
        result += [f"component={name}", f"unit={unit}", f"main_pid={main}", f"actual_pids={','.join(pids) or 'none'}", f"active_state={state.get('ActiveState', 'unknown')}", f"sub_state={state.get('SubState', 'unknown')}", f"nrestarts={state.get('NRestarts', 'unknown')}", f"managed={str(managed).lower()}", f"cgroups={';'.join(cgroups) or 'none'}", ""]
    return "\n".join(result)

def pids():
    fe = _pids("org.apache.doris.DorisFE"); be = _pids("(^|/)doris_be( |$)")
    return (fe[0] if len(fe) == 1 else "", be[0] if len(be) == 1 else "")
