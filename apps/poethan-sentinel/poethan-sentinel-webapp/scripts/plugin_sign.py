#!/usr/bin/env python3
"""Create and verify Poethan Sentinel plugin lock files and Ed25519 signatures."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


EXCLUDED = {"plugin.lock.json", "plugin.sig"}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def load_manifest(root: Path) -> dict:
    manifest = yaml.safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("plugin.yaml 顶层必须是对象")
    publisher = manifest.get("publisher")
    if not isinstance(publisher, dict) or not publisher.get("id") or not publisher.get("keyId"):
        raise ValueError("plugin.yaml 必须声明 publisher.id 和 publisher.keyId")
    return manifest


def make_lock(root: Path) -> dict:
    manifest = load_manifest(root)
    files = []
    for path in sorted((item for item in root.rglob("*") if item.is_file() and item.name not in EXCLUDED), key=lambda item: item.relative_to(root).as_posix()):
        files.append({"path": path.relative_to(root).as_posix(), "sha256": digest(path)})
    return {
        "formatVersion": 1,
        "plugin": {"id": manifest["id"], "version": manifest["version"]},
        "publisher": {"id": manifest["publisher"]["id"], "keyId": manifest["publisher"]["keyId"]},
        "files": files,
    }


def keygen(private_path: Path, public_path: Path) -> None:
    if private_path.exists() or public_path.exists():
        raise FileExistsError("目标密钥文件已存在，拒绝覆盖")
    private_key = Ed25519PrivateKey.generate()
    private_path.write_bytes(private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private_path.chmod(0o600)
    public_raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public_path.write_text(base64.b64encode(public_raw).decode("ascii") + "\n", encoding="ascii")
    print(f"private={private_path}")
    print(f"public={public_path}")
    print(f"fingerprint=SHA256:{hashlib.sha256(public_raw).hexdigest()}")


def sign(root: Path, private_path: Path) -> None:
    private_key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("私钥不是 Ed25519")
    lock = make_lock(root)
    payload = canonical(lock)
    (root / "plugin.lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "plugin.sig").write_text(base64.b64encode(private_key.sign(payload)).decode("ascii") + "\n", encoding="ascii")
    print(f"signed={root}")
    print(f"lock_sha256={hashlib.sha256(payload).hexdigest()}")


def verify(root: Path, public_path: Path) -> None:
    expected = make_lock(root)
    actual = json.loads((root / "plugin.lock.json").read_text(encoding="utf-8"))
    if expected != actual:
        raise ValueError("plugin.lock.json 与目录当前内容不一致")
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_path.read_text(encoding="ascii")))
    signature = base64.b64decode((root / "plugin.sig").read_text(encoding="ascii"))
    try:
        public.verify(signature, canonical(actual))
    except InvalidSignature as exc:
        raise ValueError("签名无效") from exc
    print(f"verified={root}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("keygen"); generate.add_argument("private", type=Path); generate.add_argument("public", type=Path)
    signer = sub.add_parser("sign"); signer.add_argument("plugin", type=Path); signer.add_argument("private", type=Path)
    verifier = sub.add_parser("verify"); verifier.add_argument("plugin", type=Path); verifier.add_argument("public", type=Path)
    args = parser.parse_args()
    if args.command == "keygen": keygen(args.private, args.public)
    elif args.command == "sign": sign(args.plugin.resolve(), args.private.resolve())
    else: verify(args.plugin.resolve(), args.public.resolve())


if __name__ == "__main__":
    main()
