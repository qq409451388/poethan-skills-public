import os
import subprocess

def command(args, timeout=20, shell=False, env=None):
    try:
        result = subprocess.run(args, shell=shell, text=True, capture_output=True, timeout=timeout, env=env)
        return result.returncode, (result.stdout + result.stderr).strip()
    except Exception as exc:
        return 127, f"collector_error={type(exc).__name__}:{exc}"

def section(name, body):
    print(f"===== SECTION: {name} =====")
    print(body.strip() if body and body.strip() else "status=no_data")
    print()

def env(name, default=""):
    return os.environ.get(name, default)
