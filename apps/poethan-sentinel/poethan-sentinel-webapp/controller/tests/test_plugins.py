from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from app import config
from app.models import ApplicationSettings
from app.plugins import is_ignored_artifact, plugin_service


def settings_for(path: Path, developer: bool = False) -> ApplicationSettings:
    return ApplicationSettings(plugin_directory=str(path), developer_mode=developer, demo_mode=True)


def plugin_directories() -> list[Path]:
    return [root for root in config.PROJECT_PLUGIN_ROOT.iterdir() if (root / "plugin.yaml").is_file()]


def test_official_plugins_are_signed_and_valid() -> None:
    expected = len(plugin_directories())
    assert expected > 0
    result = plugin_service.scan(settings_for(config.PROJECT_PLUGIN_ROOT))
    assert result.valid_count == expected
    assert result.invalid_count == 0
    assert all(item.plugin and item.plugin.trust.status == "trusted" for item in result.items)


def test_every_packaged_plugin_has_a_trusted_publisher_entry() -> None:
    """插件用的是 builtin 信任列表，缺条目会让全新 checkout 直接变成 untrusted。"""
    contract = json.loads((config.CONTRACTS_ROOT / "trusted-publishers.json").read_text(encoding="utf-8"))
    scopes = {scope for item in contract["publishers"] for scope in item.get("pluginScopes", [])}
    for directory in plugin_directories():
        manifest = yaml.safe_load((directory / "plugin.yaml").read_text(encoding="utf-8"))
        assert manifest["id"] in scopes, f"{manifest['id']} 不在任何发布者的 pluginScopes 里"


def test_tampered_signed_plugin_is_rejected(tmp_path: Path) -> None:
    source = config.PROJECT_PLUGIN_ROOT / "host-performance"
    target = tmp_path / "host-performance"
    shutil.copytree(source, target)
    (target / "main.py").write_text((target / "main.py").read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    result = plugin_service.scan(settings_for(tmp_path))
    assert result.valid_count == 0
    assert "摘要不匹配" in result.items[0].errors[0]


def test_unsigned_plugin_only_loads_in_developer_mode(tmp_path: Path) -> None:
    source = config.PROJECT_PLUGIN_ROOT / "network-diagnostic"
    target = tmp_path / "network-diagnostic"
    shutil.copytree(source, target)
    (target / "plugin.lock.json").unlink()
    (target / "plugin.sig").unlink()
    strict = plugin_service.scan(settings_for(tmp_path, False))
    developer = plugin_service.scan(settings_for(tmp_path, True))
    assert strict.valid_count == 0
    assert developer.valid_count == 1
    assert developer.items[0].plugin
    assert developer.items[0].plugin.trust.status == "unsigned"


def test_packaged_plugins_are_readable_by_other_users() -> None:
    """插件会以 root 属主装到 /opt，运行者是别的账号：文件必须 other 可读。

    本地写文件默认 0600，`chmod +x` 又会得到 0711（只有执行位没有读位），
    这种权限上传后 bash 只会报 "Permission denied"，极难定位，所以在仓库层面挡住。
    """
    for root in plugin_directories():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if is_ignored_artifact(relative):
                continue
            mode = path.stat().st_mode & 0o777
            assert mode & 0o004, f"{relative} 其他用户不可读：mode={oct(mode)}"
            if path.name.endswith(".sh"):
                assert mode & 0o001, f"{relative} 其他用户不可执行：mode={oct(mode)}"


def test_lock_covers_every_package_file() -> None:
    for root in plugin_directories():
        lock = json.loads((root / "plugin.lock.json").read_text(encoding="utf-8"))
        expected = {
            path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() and path.name not in {"plugin.lock.json", "plugin.sig"}
            and not is_ignored_artifact(path.relative_to(root).as_posix())
        }
        assert {item["path"] for item in lock["files"]} == expected


def test_local_build_artifacts_do_not_invalidate_a_signed_plugin(tmp_path: Path) -> None:
    """跑一次插件就会生成 __pycache__；它不该让签名插件变成"有未记录文件"。"""
    target = tmp_path / "host-performance"
    shutil.copytree(config.PROJECT_PLUGIN_ROOT / "host-performance", target)
    (target / "__pycache__").mkdir()
    (target / "__pycache__" / "main.cpython-314.pyc").write_bytes(b"stale cache")
    (target / ".DS_Store").write_bytes(b"junk")
    result = plugin_service.scan(settings_for(tmp_path))
    assert result.invalid_count == 0
    assert result.items[0].plugin
    assert result.items[0].plugin.trust.status == "trusted"


def test_local_caches_are_never_shipped_to_the_server(tmp_path: Path) -> None:
    """本机跑过插件会留下 __pycache__；它不能进上传包，也不能进安装目录。"""
    import tarfile

    from app.ssh import ssh_service
    from app.storage import JSONStore

    source = tmp_path / "source" / "host-performance"
    shutil.copytree(config.PROJECT_PLUGIN_ROOT / "host-performance", source)
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "main.cpython-314.pyc").write_bytes(b"cache")
    (source / ".DS_Store").write_bytes(b"junk")

    archive_path, _ = ssh_service._archive(source)
    try:
        with tarfile.open(archive_path) as archive:
            names = archive.getnames()
    finally:
        archive_path.unlink(missing_ok=True)
    assert not any("__pycache__" in name or name.endswith(".pyc") or ".DS_Store" in name for name in names)
    assert "main.py" in names

    destination = tmp_path / "data" / "plugins" / "host-performance" / "1.0.0"
    destination.parent.mkdir(parents=True)
    JSONStore._copy_package(source, destination)
    assert not (destination / "__pycache__").exists()
    assert not (destination / ".DS_Store").exists()
    assert (destination / "main.py").is_file()


def test_lock_entries_for_removed_local_caches_are_tolerated(tmp_path: Path) -> None:
    """旧签名脚本把 .pyc 记进 lock 时，删掉本地缓存也不能报"文件不存在"。"""
    target = tmp_path / "host-performance"
    shutil.copytree(config.PROJECT_PLUGIN_ROOT / "host-performance", target)
    lock_path = target / "plugin.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["files"].append({"path": "__pycache__/main.cpython-314.pyc", "sha256": "0" * 64})
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # lock 内容变了，签名自然失效；这里只关心文件摘要检查不再报"缺失"。
    result = plugin_service.scan(settings_for(tmp_path))
    assert "不存在" not in result.items[0].errors[0]
