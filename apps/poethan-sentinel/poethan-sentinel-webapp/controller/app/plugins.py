from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator

from . import config
from .models import ApplicationSettings, PluginPackage, PluginScanItem, PluginScanResponse, PluginTrust


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,63}$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
FIELD_TYPES = {"text", "path", "integer", "url", "password", "boolean", "choice"}
MAX_PACKAGE_BYTES = 100 * 1024 * 1024


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_child(root: Path, relative: str, must_exist: bool = True) -> Path:
    if not relative or relative.startswith("/") or "\\" in relative:
        raise ValueError(f"不安全的相对路径：{relative!r}")
    target = (root / relative).resolve()
    resolved_root = root.resolve()
    if target == resolved_root or resolved_root not in target.parents:
        raise ValueError(f"路径越过插件根目录：{relative}")
    if must_exist and not target.is_file():
        raise ValueError(f"文件不存在：{relative}")
    return target


class PluginService:
    def __init__(self) -> None:
        self.schema = json.loads((config.CONTRACTS_ROOT / "plugin.schema.json").read_text(encoding="utf-8"))
        self.schema_validator = Draft202012Validator(self.schema)

    def scan(self, settings: ApplicationSettings) -> PluginScanResponse:
        root = Path(settings.plugin_directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        results: list[PluginScanItem] = []
        seen: set[tuple[str, str]] = set()
        for directory in self._candidates(root):
            try:
                package = self.validate(directory, settings.developer_mode)
                key = (package.id, package.version)
                if key in seen:
                    raise ValueError(f"插件 ID 与版本重复：{package.id}@{package.version}")
                seen.add(key)
                results.append(PluginScanItem(directory=str(directory), valid=True, plugin=package))
            except Exception as exc:
                results.append(PluginScanItem(directory=str(directory), valid=False, errors=[str(exc)]))
        results.sort(key=lambda item: (not item.valid, item.plugin.name if item.plugin else Path(item.directory).name))
        return PluginScanResponse(items=results, valid_count=sum(item.valid for item in results), invalid_count=sum(not item.valid for item in results))

    def find(self, settings: ApplicationSettings, plugin_id: str, version: str) -> PluginPackage:
        scan = self.scan(settings)
        for item in scan.items:
            if item.plugin and item.plugin.id == plugin_id and item.plugin.version == version:
                return item.plugin
        raise FileNotFoundError(f"找不到有效插件 {plugin_id}@{version}")

    def validate(self, directory: Path, developer_mode: bool) -> PluginPackage:
        directory = directory.resolve()
        manifest_path = directory / "plugin.yaml"
        if not manifest_path.is_file():
            raise ValueError("插件根目录缺少 plugin.yaml")
        self._validate_tree(directory)
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"plugin.yaml 解析失败：{exc}") from exc
        if not isinstance(manifest, dict):
            raise ValueError("plugin.yaml 顶层必须是对象")
        if "schemaVersion" not in manifest:
            manifest["schemaVersion"] = 1
        schema_errors = sorted(self.schema_validator.iter_errors(manifest), key=lambda error: list(error.path))
        if schema_errors:
            error = schema_errors[0]
            location = ".".join(str(part) for part in error.path) or "plugin.yaml"
            raise ValueError(f"清单字段 {location} 无效：{error.message}")
        if not ID_PATTERN.fullmatch(str(manifest["id"])):
            raise ValueError("插件 ID 格式无效")
        if not VERSION_PATTERN.fullmatch(str(manifest["version"])):
            raise ValueError("插件版本必须使用语义化版本")
        safe_child(directory, str(manifest["entrypoint"]))
        mode_ids = {str(mode.get("id")) for mode in manifest.get("modes", [])}
        if manifest.get("defaultMode") not in mode_ids:
            raise ValueError("defaultMode 必须引用 modes 中存在的模式")
        keys: set[str] = set()
        fields = manifest.get("configuration", {}).get("fields", [])
        for field in fields:
            key = str(field.get("key", ""))
            if key in keys:
                raise ValueError(f"配置字段重复：{key}")
            keys.add(key)
            if field.get("type") not in FIELD_TYPES:
                raise ValueError(f"字段 {key} 使用了不支持的类型")
            if field.get("type") == "choice" and not field.get("options"):
                raise ValueError(f"choice 字段 {key} 必须提供 options")
        report = manifest.get("report")
        if report:
            schema_path = safe_child(directory, str(report["schema"]))
            safe_child(directory, str(report["template"]))
            try:
                json.loads(schema_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(f"报告 Schema 不是有效 JSON：{exc}") from exc
        trust = self._verify_trust(directory, manifest, developer_mode)
        if trust.status in {"invalid", "untrusted"}:
            raise ValueError(trust.message)
        return PluginPackage(
            id=str(manifest["id"]), name=str(manifest["name"]), description=str(manifest.get("description", "")),
            version=str(manifest["version"]), entrypoint=str(manifest["entrypoint"]), language=str(manifest.get("language", "bash")),
            output_limit=int(manifest.get("outputLimit", 1_000_000)), default_mode=str(manifest["defaultMode"]),
            modes=list(manifest.get("modes", [])), fields=list(fields), report=report,
            permissions=dict(manifest.get("permissions", {})), directory=str(directory), trust=trust,
        )

    def import_directory(self, source: Path, settings: ApplicationSettings) -> PluginPackage:
        package = self.validate(source, settings.developer_mode)
        root = Path(settings.plugin_directory).expanduser().resolve()
        destination = root / package.id / package.version
        if destination.exists():
            existing = self.validate(destination, settings.developer_mode)
            if existing.trust.lock_digest != package.trust.lock_digest:
                raise ValueError(f"{package.id}@{package.version} 已存在且内容摘要不同，请提升版本")
            return existing
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        return self.validate(destination, settings.developer_mode)

    def _candidates(self, root: Path) -> list[Path]:
        candidates: list[Path] = []
        for child in sorted((path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")), key=lambda path: path.name):
            if (child / "plugin.yaml").is_file():
                candidates.append(child)
                continue
            nested = sorted((path for path in child.iterdir() if path.is_dir() and not path.name.startswith(".")), key=lambda path: path.name)
            if any((path / "plugin.yaml").is_file() for path in nested):
                candidates.extend(nested)
            else:
                candidates.append(child)
        return candidates

    def _validate_tree(self, directory: Path) -> None:
        total = 0
        for root, folders, files in os.walk(directory, followlinks=False):
            root_path = Path(root)
            for name in [*folders, *files]:
                path = root_path / name
                if path.is_symlink():
                    raise ValueError(f"插件包不能包含符号链接：{path.relative_to(directory)}")
            for name in files:
                total += (root_path / name).stat().st_size
                if total > MAX_PACKAGE_BYTES:
                    raise ValueError("插件包超过 100 MB 限制")

    def _verify_trust(self, directory: Path, manifest: dict[str, Any], developer_mode: bool) -> PluginTrust:
        lock_path, signature_path = directory / "plugin.lock.json", directory / "plugin.sig"
        if not lock_path.is_file() or not signature_path.is_file():
            if developer_mode:
                return PluginTrust(status="unsigned", lock_digest=self._directory_digest(directory), message="开发者模式：插件未签名")
            return PluginTrust(status="untrusted", message="插件缺少 plugin.lock.json 或 plugin.sig；请使用受信发布者签名，或仅在开发者模式调试")
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return PluginTrust(status="invalid", message=f"plugin.lock.json 无效：{exc}")
        canonical = canonical_json(lock)
        lock_digest = hashlib.sha256(canonical).hexdigest()
        plugin = lock.get("plugin", {})
        publisher = lock.get("publisher", {})
        if plugin.get("id") != manifest.get("id") or plugin.get("version") != manifest.get("version"):
            return PluginTrust(status="invalid", lock_digest=lock_digest, message="lock 文件中的插件 ID 或版本与 plugin.yaml 不一致")
        manifest_publisher = manifest.get("publisher", {})
        if publisher.get("id") != manifest_publisher.get("id") or publisher.get("keyId") != manifest_publisher.get("keyId"):
            return PluginTrust(status="invalid", lock_digest=lock_digest, message="lock 文件中的发布者与 plugin.yaml 不一致")
        file_errors = self._verify_locked_files(directory, lock)
        if file_errors:
            return PluginTrust(status="invalid", lock_digest=lock_digest, message=file_errors[0])
        trusted = self._trusted_publishers()
        key = next((item for item in trusted if item.get("id") == publisher.get("id") and item.get("keyId") == publisher.get("keyId")), None)
        if not key:
            return PluginTrust(status="untrusted", publisher_id=publisher.get("id"), key_id=publisher.get("keyId"), lock_digest=lock_digest, message="插件签名使用了未知发布者，请先导入并确认发布者公钥")
        if not any(fnmatch.fnmatch(str(manifest["id"]), scope) for scope in key.get("pluginScopes", ["*"])):
            return PluginTrust(status="invalid", publisher_id=publisher.get("id"), key_id=publisher.get("keyId"), lock_digest=lock_digest, message="发布者公钥无权签署此插件 ID")
        try:
            public_bytes = base64.b64decode(key["publicKey"])
            signature = base64.b64decode(signature_path.read_text(encoding="ascii").strip())
            Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, canonical)
        except (ValueError, KeyError, InvalidSignature) as exc:
            return PluginTrust(status="invalid", publisher_id=publisher.get("id"), key_id=publisher.get("keyId"), lock_digest=lock_digest, message=f"插件数字签名验证失败：{type(exc).__name__}")
        fingerprint = hashlib.sha256(public_bytes).hexdigest()
        return PluginTrust(status="trusted", publisher_id=publisher.get("id"), key_id=publisher.get("keyId"), fingerprint=fingerprint, lock_digest=lock_digest, message="签名有效，插件内容未被修改")

    def _verify_locked_files(self, directory: Path, lock: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        expected_paths: set[str] = set()
        files = lock.get("files")
        if not isinstance(files, list) or not files:
            return ["plugin.lock.json 未包含文件摘要"]
        for item in files:
            relative = str(item.get("path", ""))
            try:
                file_path = safe_child(directory, relative)
            except ValueError as exc:
                errors.append(str(exc)); continue
            expected_paths.add(relative)
            actual = sha256_file(file_path)
            if actual != item.get("sha256"):
                errors.append(f"文件摘要不匹配：{relative}")
        actual_paths = {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file() and path.name not in {"plugin.lock.json", "plugin.sig"}}
        extra = sorted(actual_paths - expected_paths)
        missing = sorted(expected_paths - actual_paths)
        if extra:
            errors.append(f"lock 文件未记录这些文件：{', '.join(extra[:5])}")
        if missing:
            errors.append(f"lock 文件记录的文件不存在：{', '.join(missing[:5])}")
        return errors

    def _trusted_publishers(self) -> list[dict[str, Any]]:
        bundled = json.loads((config.CONTRACTS_ROOT / "trusted-publishers.json").read_text(encoding="utf-8")).get("publishers", [])
        user: list[dict[str, Any]] = []
        if config.USER_PUBLISHERS_FILE.exists():
            try:
                user = json.loads(config.USER_PUBLISHERS_FILE.read_text(encoding="utf-8")).get("publishers", [])
            except Exception:
                user = []
        return [*bundled, *user]

    def _directory_digest(self, directory: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted((path for path in directory.rglob("*") if path.is_file()), key=lambda path: path.relative_to(directory).as_posix()):
            relative = path.relative_to(directory).as_posix()
            digest.update(relative.encode("utf-8")); digest.update(b"\0"); digest.update(bytes.fromhex(sha256_file(path)))
        return digest.hexdigest()


plugin_service = PluginService()
