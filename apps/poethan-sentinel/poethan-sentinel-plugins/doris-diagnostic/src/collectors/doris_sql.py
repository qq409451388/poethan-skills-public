import os
from src.util import command, env

def collect():
    mysql = ["mysql", "--batch", "--raw", "--skip-column-names", "-h", env("DORIS_HOST", "127.0.0.1"), "-P", env("DORIS_PORT", "9030"), "-u", env("DORIS_USER", "root")]
    process_env = os.environ.copy(); process_env["MYSQL_PWD"] = env("DORIS_PASSWORD")
    statements = ["SELECT VERSION()", "SHOW FRONTENDS", "SHOW BACKENDS", 'SHOW PROC "/current_queries"', "SHOW PROCESSLIST", "SHOW VARIABLES LIKE 'enable_two_phase_read_opt'", "SHOW VARIABLES LIKE 'enable_auto_analyze'"]
    database = env("DORIS_DATABASE")
    if database: statements.append(f"SHOW STREAM LOAD FROM `{database.replace('`', '')}` ORDER BY StartTime DESC LIMIT 20")
    rows = []
    for sql in statements:
        code, output = command(mysql + ["-e", sql], timeout=30, env=process_env)
        rows.append(f"sql={sql}\nexit={code}\n{output}")
    return "\n\n".join(rows)
