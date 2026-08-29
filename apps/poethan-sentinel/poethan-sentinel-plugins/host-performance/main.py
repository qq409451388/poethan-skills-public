import os, subprocess, sys

def run(command):
    try: return subprocess.run(command, shell=True, text=True, capture_output=True, timeout=15).stdout.strip()
    except Exception as exc: return f"collector_error={type(exc).__name__}:{exc}"
def section(name, body): print(f"===== SECTION: {name} =====\n{body}\n")

mode = sys.argv[1] if len(sys.argv) > 1 else "standard"
cores = os.cpu_count() or 1
loads = os.getloadavg()
mem = {}
try:
    for line in open('/proc/meminfo', encoding='utf-8'):
        key, value = line.split(':', 1); mem[key] = int(value.strip().split()[0])
except Exception: pass
available_percent = mem.get('MemAvailable', 0) * 100 / max(mem.get('MemTotal', 1), 1)
section('HOST', f"hostname={run('hostname')}\ncpu_cores={cores}\nuptime={run('uptime -p || uptime')}")
section('RESOURCE_FACTS', f"load1={loads[0]:.2f}\nload5={loads[1]:.2f}\nload15={loads[2]:.2f}\nload1_per_core={loads[0]/cores:.2f}\nmemory_total_kb={mem.get('MemTotal', 0)}\nmemory_available_kb={mem.get('MemAvailable', 0)}\nmemory_available_percent={available_percent:.1f}\nswap_total_kb={mem.get('SwapTotal', 0)}\nswap_free_kb={mem.get('SwapFree', 0)}")
load_limit = float(os.getenv('LOAD_PER_CORE_WARNING', '1.0')); memory_limit = float(os.getenv('MEMORY_AVAILABLE_WARNING_PERCENT', '10'))
checks = [f"check_id=HOST-001\nstatus={'failed' if loads[0]/cores > load_limit else 'passed'}\nvalue={loads[0]/cores:.2f}\nthreshold={load_limit}", f"check_id=HOST-002\nstatus={'failed' if available_percent < memory_limit else 'passed'}\nvalue={available_percent:.1f}\nthreshold={memory_limit}"]
section('CHECKS', '\n\n'.join(checks))
if mode == 'standard':
    section('TOP_PROCESSES', run("ps -eo pid,user,pcpu,pmem,rss,vsz,comm,args --sort=-pcpu | head -30"))
    section('VMSTAT', run("vmstat 1 3 2>/dev/null || true"))
