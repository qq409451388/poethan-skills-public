import os, subprocess, sys, time

def run(command):
    try: return subprocess.run(command, shell=True, text=True, capture_output=True, timeout=15).stdout.strip()
    except Exception as exc: return f"collector_error={type(exc).__name__}:{exc}"
def sample():
    values = {}
    try:
        for line in open('/proc/net/dev', encoding='utf-8').read().splitlines()[2:]:
            name, raw = line.split(':', 1); fields = raw.split(); values[name.strip()] = (int(fields[0]), int(fields[8]))
    except Exception: pass
    return values
def section(name, body): print(f"===== SECTION: {name} =====\n{body}\n")

mode = sys.argv[1] if len(sys.argv) > 1 else 'standard'; seconds = max(1, int(os.getenv('NETWORK_SAMPLE_SECONDS', '2'))); warning = float(os.getenv('NETWORK_WARNING_MBPS', '100'))
before = sample(); time.sleep(seconds); after = sample(); rows=[]; checks=[]
for name, end in sorted(after.items()):
    start = before.get(name, end); rx=(end[0]-start[0])*8/seconds/1_000_000; tx=(end[1]-start[1])*8/seconds/1_000_000; peak=max(rx,tx)
    rows += [f"interface={name}",f"rx_mbps={rx:.3f}",f"tx_mbps={tx:.3f}",""]
    checks.append(f"check_id=NETWORK-001\ninterface={name}\nstatus={'failed' if peak > warning else 'passed'}\npeak_mbps={peak:.3f}\nthreshold_mbps={warning}")
section('NETWORK_RATE', '\n'.join(rows) or 'status=no_data'); section('CHECKS', '\n\n'.join(checks) or 'status=no_data')
if mode == 'standard':
    section('SOCKET_SUMMARY', run('ss -s 2>/dev/null || netstat -s 2>/dev/null || true'))
    section('LISTENING_PORTS', run('ss -lntup 2>/dev/null || netstat -lnt 2>/dev/null || true'))
