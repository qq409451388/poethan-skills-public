from __future__ import annotations

import base64
import hashlib
import io
import json
import shlex
import socket
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import paramiko

from . import config
from .models import ConnectionTestResult, PluginPackage, ServerProfile
from .plugins import sha256_file
from .secrets import secrets, server_password_account


@dataclass
class Target:
    host: str
    user: str | None
    port: int
    identity_file: str | None
    alias: str | None = None


@dataclass
class ExecutionResult:
    exit_code: int
    output: str
    remote_plugin: str
    archive_sha256: str


class SSHService:
    def resolve(self, server: ServerProfile) -> Target:
        if server.authentication.value == "alias":
            ssh_config = paramiko.SSHConfig()
            ssh_path = Path.home() / ".ssh" / "config"
            if ssh_path.exists():
                with ssh_path.open(encoding="utf-8") as handle:
                    ssh_config.parse(handle)
            values = ssh_config.lookup(server.alias)
            identity = values.get("identityfile", [None])[0]
            return Target(
                host=values.get("hostname", server.alias), user=values.get("user") or None,
                port=int(values.get("port", 22)), identity_file=str(Path(identity).expanduser()) if identity else None,
                alias=server.alias,
            )
        return Target(
            host=server.host, user=server.user or None, port=server.port,
            identity_file=str(Path(server.identity_file).expanduser()) if server.identity_file else None,
        )

    def test(self, server: ServerProfile, accept_host_key: bool = False, password_override: str | None = None) -> ConnectionTestResult:
        if server.authentication.value == "demo":
            return ConnectionTestResult(ok=True, message="演示服务器连接正常", target="demo@sentinel", latency_ms=12)
        target = self.resolve(server)
        started = time.monotonic()
        try:
            client = self.connect(server, accept_host_key=accept_host_key, password_override=password_override)
            _, stdout, _ = client.exec_command("printf '%s@%s' \"$(id -un)\" \"$(hostname)\"", timeout=8)
            identity = stdout.read().decode("utf-8", "replace")
            client.close()
            return ConnectionTestResult(ok=True, message="连接成功", target=identity, latency_ms=int((time.monotonic() - started) * 1000))
        except paramiko.BadHostKeyException as exc:
            return ConnectionTestResult(ok=False, message="服务器主机密钥与已保存记录不一致，已阻止连接", target=target.host, host_key_changed=True, fingerprint=self._fingerprint(exc.key))
        except paramiko.SSHException as exc:
            text = str(exc)
            if "not found in known_hosts" in text or "Unknown server" in text:
                fingerprint = self.probe_fingerprint(target)
                return ConnectionTestResult(ok=False, message="首次连接需要确认服务器主机密钥", target=target.host, host_key_required=True, fingerprint=fingerprint)
            return ConnectionTestResult(ok=False, message=f"SSH 连接失败：{text}", target=target.host)
        except Exception as exc:
            return ConnectionTestResult(ok=False, message=f"连接失败：{exc}", target=target.host)

    def connect(self, server: ServerProfile, accept_host_key: bool = False, password_override: str | None = None) -> paramiko.SSHClient:
        target = self.resolve(server)
        if not target.host:
            raise ValueError("服务器地址不能为空")
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        if config.KNOWN_HOSTS_FILE.exists():
            client.load_host_keys(str(config.KNOWN_HOSTS_FILE))
        if accept_host_key:
            key = self._probe_key(target)
            key_name = target.host if target.port == 22 else f"[{target.host}]:{target.port}"
            client.get_host_keys().add(key_name, key.get_name(), key)
            config.KNOWN_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            client.save_host_keys(str(config.KNOWN_HOSTS_FILE))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        password = password_override or secrets.get(server_password_account(server.id))
        client.connect(
            hostname=target.host, port=target.port, username=target.user,
            password=password, key_filename=target.identity_file,
            allow_agent=True, look_for_keys=True, timeout=10, banner_timeout=10, auth_timeout=10,
        )
        return client

    def probe_fingerprint(self, target: Target) -> str:
        return self._fingerprint(self._probe_key(target))

    def execute_plugin(
        self, server: ServerProfile, plugin: PluginPackage, run_id: str, mode: str,
        values: dict[str, str], secret_values: dict[str, str], output_limit: int,
        cancel: threading.Event, on_output: Callable[[str], None],
    ) -> ExecutionResult:
        plugin_root = Path(plugin.directory)
        remote_run = f"/tmp/poethan-sentinel-{run_id}"
        plugin_home = values.get("POETHAN_PLUGIN_HOME", "/opt/poethan-sentinel/plugins")
        digest = plugin.trust.lock_digest or self._tree_digest(plugin_root)
        remote_plugin = f"{plugin_home.rstrip('/')}/{plugin.id}/{plugin.version}/{digest[:16]}"
        client = self.connect(server)
        archive_path: Path | None = None
        archive_sha = "cached:" + digest
        try:
            self._exec_checked(client, f"mkdir -p {shlex.quote(remote_run)} && chmod 700 {shlex.quote(remote_run)}")
            cached = self._exec_checked(client, f"test -d {shlex.quote(remote_plugin)} && printf cached", allow_failure=True).strip() == "cached"
            if not cached:
                archive_path, archive_sha = self._archive(plugin_root)
                sftp = client.open_sftp()
                sftp.put(str(archive_path), f"{remote_run}/plugin.tgz")
                sftp.close()
                remote_sha = self._exec_checked(client, f"sha256sum {shlex.quote(remote_run + '/plugin.tgz')} | awk '{{print $1}}'").strip()
                if remote_sha != archive_sha:
                    raise RuntimeError("插件上传后的 SHA-256 与本机不一致")
                on_output(f"远程归档摘要已确认：{archive_sha[:16]}…")
                verify_commands = self._remote_verify_commands(plugin_root, f"{remote_run}/package")
                destination = shlex.quote(remote_plugin)
                parent = shlex.quote(str(Path(remote_plugin).parent))
                install = f"""
set -e
rm -rf {shlex.quote(remote_run + '/package')}
mkdir -p {shlex.quote(remote_run + '/package')}
tar -xzf {shlex.quote(remote_run + '/plugin.tgz')} -C {shlex.quote(remote_run + '/package')}
{verify_commands}
if [ ! -d {destination} ]; then
  (mkdir -p {parent} && cp -R {shlex.quote(remote_run + '/package')} {destination}) || \
  (sudo -n mkdir -p {parent} && sudo -n cp -R {shlex.quote(remote_run + '/package')} {destination})
fi
"""
                self._exec_checked(client, install, timeout=60)
                on_output(f"插件已同步：{plugin.id}@{plugin.version} · {digest[:12]}")
            else:
                on_output(f"复用服务器插件缓存：{plugin.id}@{plugin.version} · {digest[:12]}")
            sftp = client.open_sftp()
            config_text = self._environment(values | secret_values)
            with sftp.file(f"{remote_run}/config.env", "w") as handle:
                handle.write(config_text)
            sftp.chmod(f"{remote_run}/config.env", 0o600)
            sftp.close()
            result_path = f"{remote_run}/result.txt"
            command = f"chmod +x {shlex.quote(remote_plugin + '/' + plugin.entrypoint)} && POETHAN_CONFIG_FILE={shlex.quote(remote_run + '/config.env')} {shlex.quote(remote_plugin + '/' + plugin.entrypoint)} {shlex.quote(mode)} > {shlex.quote(result_path)} 2>&1"
            exit_code, control_output = self._stream_command(client, command, cancel, on_output, 64_000)
            output = self._download_result(client, result_path, output_limit)
            on_output(f"诊断结果已从服务器临时文件下载：{len(output.encode('utf-8'))} bytes")
            if control_output.strip():
                output += "\n\n===== SECTION: CONTROLLER =====\n" + control_output
            return ExecutionResult(exit_code=exit_code, output=output, remote_plugin=remote_plugin, archive_sha256=archive_sha)
        finally:
            try:
                self._exec_checked(client, f"rm -rf {shlex.quote(remote_run)}", timeout=10, allow_failure=True)
            finally:
                client.close()
                if archive_path:
                    archive_path.unlink(missing_ok=True)

    def _archive(self, root: Path) -> tuple[Path, str]:
        handle = tempfile.NamedTemporaryFile(prefix="poethan-plugin-", suffix=".tgz", delete=False)
        handle.close()
        path = Path(handle.name)
        with tarfile.open(path, "w:gz") as archive:
            for child in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
                archive.add(child, arcname=child.relative_to(root).as_posix(), recursive=False)
        return path, sha256_file(path)

    @staticmethod
    def _tree_digest(root: Path) -> str:
        value = hashlib.sha256()
        for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix()):
            value.update(path.relative_to(root).as_posix().encode("utf-8"))
            value.update(bytes.fromhex(sha256_file(path)))
        return value.hexdigest()

    @staticmethod
    def _download_result(client: paramiko.SSHClient, remote_path: str, output_limit: int) -> str:
        sftp = client.open_sftp()
        try:
            with sftp.file(remote_path, "rb") as handle:
                payload = handle.read(output_limit + 1)
        finally:
            sftp.close()
        if len(payload) > output_limit:
            return payload[:output_limit].decode("utf-8", "replace") + "\n\n[Poethan Sentinel: 输出超过插件限制，已截断]"
        return payload.decode("utf-8", "replace")

    def _remote_verify_commands(self, root: Path, remote_root: str) -> str:
        lock_path = root / "plugin.lock.json"
        if lock_path.exists():
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lines = []
            for item in lock.get("files", []):
                target = f"{remote_root}/{item['path']}"
                lines.append(f"printf '%s  %s\\n' {shlex.quote(item['sha256'])} {shlex.quote(target)} | sha256sum -c -")
            return "\n".join(lines)
        lines = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            target = f"{remote_root}/{relative}"
            lines.append(f"printf '%s  %s\\n' {shlex.quote(sha256_file(path))} {shlex.quote(target)} | sha256sum -c -")
        return "\n".join(lines)

    def _stream_command(self, client: paramiko.SSHClient, command: str, cancel: threading.Event, on_output: Callable[[str], None], output_limit: int) -> tuple[int, str]:
        transport = client.get_transport()
        if transport is None:
            raise RuntimeError("SSH 连接已关闭")
        channel = transport.open_session(timeout=10)
        channel.set_combine_stderr(True)
        channel.exec_command(command)
        chunks: list[str] = []
        total = 0
        started = time.monotonic()
        while not channel.exit_status_ready() or channel.recv_ready():
            if cancel.is_set():
                channel.close()
                raise InterruptedError("诊断已由用户停止")
            if time.monotonic() - started > 900:
                channel.close()
                raise TimeoutError("诊断脚本运行超过 15 分钟")
            if channel.recv_ready():
                text = channel.recv(32768).decode("utf-8", "replace")
                if total < output_limit:
                    accepted = text[: output_limit - total]
                    chunks.append(accepted); total += len(accepted); on_output(accepted)
            else:
                time.sleep(0.05)
        return channel.recv_exit_status(), "".join(chunks)

    def _exec_checked(self, client: paramiko.SSHClient, command: str, timeout: int = 30, allow_failure: bool = False) -> str:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        output = stdout.read().decode("utf-8", "replace") + stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if code != 0 and not allow_failure:
            raise RuntimeError(output.strip() or f"远程命令退出码 {code}")
        return output

    def _probe_key(self, target: Target) -> paramiko.PKey:
        transport = paramiko.Transport((target.host, target.port))
        try:
            transport.start_client(timeout=8)
            return transport.get_remote_server_key()
        finally:
            transport.close()

    @staticmethod
    def _fingerprint(key: paramiko.PKey) -> str:
        value = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
        return f"SHA256:{value}"

    @staticmethod
    def _environment(values: dict[str, str]) -> str:
        lines = []
        for key, value in sorted(values.items()):
            if not key.replace("_", "").isalnum() or not key[0].isalpha():
                continue
            lines.append(f"{key}={shlex.quote(str(value))}")
        return "\n".join(lines) + "\n"


ssh_service = SSHService()
