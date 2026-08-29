from src.util import command
from src.collectors.process import pids

def collect():
    fe, be = pids(); target = ",".join(x for x in [fe, be] if x)
    _, overall = command("ps -eo pid,user,pcpu,pmem,rss,vsz,comm,args --sort=-pcpu | head -30", shell=True)
    detail = ""
    if target: _, detail = command(["ps", "-p", target, "-o", "pid,etime,pcpu,pmem,rss,vsz,args"])
    return f"top_processes:\n{overall}\n\ndoris_processes:\n{detail or 'none'}"
