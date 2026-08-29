from __future__ import annotations

import os
from threading import RLock


SERVICE = "dev.poethan.sentinel.web"


class SecretStore:
    def __init__(self) -> None:
        self._memory: dict[str, str] = {}
        self._lock = RLock()
        self._test_mode = os.getenv("POETHAN_SENTINEL_TESTING") == "1"

    def set(self, account: str, value: str) -> None:
        if not value:
            return
        with self._lock:
            if self._test_mode:
                self._memory[account] = value
                return
            import keyring
            keyring.set_password(SERVICE, account, value)

    def get(self, account: str) -> str | None:
        with self._lock:
            if self._test_mode:
                return self._memory.get(account)
            import keyring
            return keyring.get_password(SERVICE, account)

    def delete(self, account: str) -> None:
        with self._lock:
            if self._test_mode:
                self._memory.pop(account, None)
                return
            import keyring
            try:
                keyring.delete_password(SERVICE, account)
            except keyring.errors.PasswordDeleteError:
                pass


secrets = SecretStore()


def server_password_account(server_id: str) -> str:
    return f"server:{server_id}:password"


def plugin_secret_account(server_id: str, plugin_id: str, key: str) -> str:
    return f"plugin:{server_id}:{plugin_id}:{key}"
