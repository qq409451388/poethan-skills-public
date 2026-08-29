import time
from collections import defaultdict
from src.util import command
from src.collectors.process import pids

def collect():
    _, be = pids()
    if not be: return "status=skipped\nreason=single_be_pid_not_found", [], 0.0
    samples = defaultdict(list); names = {}
    for index in range(3):
        _, output = command(["ps", "-L", "-p", be, "-o", "tid=,pcpu=,stat=,comm="])
        for line in output.splitlines():
            fields = line.split(None, 3)
            if len(fields) == 4:
                tid, cpu, _, name = fields
                try: samples[tid].append(float(cpu)); names[tid] = name
                except ValueError: pass
        if index < 2: time.sleep(2)
    hot = []
    for tid, values in samples.items():
        avg = sum(values) / len(values); maximum = max(values)
        if avg > 10: hot.append((avg, tid, names.get(tid, "unknown"), values))
    hot.sort(reverse=True)
    rows = [f"be_pid={be}"]
    for avg, tid, name, values in hot[:20]: rows += [f"tid={tid}", f"name={name}", f"cpu_samples={','.join(f'{v:.1f}' for v in values)}", f"avg_cpu={avg:.1f}", f"max_cpu={max(values):.1f}", f"persistent_hot={str(len(values) == 3 and min(values) > 50).lower()}", ""]
    _, process_cpu = command(["ps", "-p", be, "-o", "pcpu="])
    try: be_cpu = float(process_cpu.strip())
    except ValueError: be_cpu = 0.0
    return "\n".join(rows), hot, be_cpu
