def evaluate(text):
    blocks = text.split("\n\n"); rows = []
    for block in blocks:
        values = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        main = values.get("main_pid", "0"); actual = values.get("actual_pids", "none")
        if main == "0" and actual == "none": rows.append(f"check_id=DORIS-001\nstatus=failed\ncomponent={values.get('component')}\nreason=service_not_running")
        elif values.get("managed") == "false": rows.append(f"check_id=DORIS-002\nstatus=failed\ncomponent={values.get('component')}\nreason=actual_pid_not_equal_systemd_main_pid\nmain_pid={main}\nactual_pids={actual}")
    return "\n\n".join(rows) or "check_id=DORIS-001,DORIS-002\nstatus=passed"
