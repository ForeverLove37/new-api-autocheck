"""Small helpers for encrypting account secrets and signing browser tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, data_dir: Path, supplied_key: str | None) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        key_file = data_dir / ".encryption_key"
        if supplied_key:
            key = supplied_key.encode("utf-8")
        elif key_file.exists():
            key = key_file.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            _write_private_file(key_file, key + b"\n")
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("AUTOCHECK_ENCRYPTION_KEY is not a valid Fernet key.") from exc
        self.signing_key = hashlib.sha256(key).digest()

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Stored secret cannot be decrypted with the configured encryption key.") from exc


class AuthManager:
    """Password verification and short-lived HMAC-signed bearer tokens."""

    def __init__(
        self,
        *,
        data_dir: Path,
        configured_password: str | None,
        signing_key: bytes,
        auth_required: bool,
    ) -> None:
        self.auth_required = auth_required
        self._password_file = data_dir / ".admin_password"
        self._root_signing_key = signing_key
        if auth_required:
            self._password = self._load_or_create_password(configured_password)
            self._signing_key = self._derive_signing_key(self._password)
        else:
            self._password = ""
            self._signing_key = signing_key

    def _load_or_create_password(self, configured_password: str | None) -> str:
        if self._password_file.exists():
            return self._password_file.read_text(encoding="utf-8").strip()
        if configured_password:
            return configured_password
        password = secrets.token_urlsafe(24)
        _write_private_file(self._password_file, (password + "\n").encode("utf-8"))
        # This is intentionally emitted once so an operator can recover the
        # bootstrap password from the protected service journal.
        print("AutoCheck generated an admin password. Read data/.admin_password or set AUTOCHECK_ADMIN_PASSWORD.")
        return password

    def change_password(self, current_password: str, new_password: str) -> str | None:
        if not self.auth_required:
            raise ValueError("Administrator password authentication is disabled.")
        if not hmac.compare_digest(current_password, self._password):
            return None
        if hmac.compare_digest(new_password, self._password):
            raise ValueError("The new administrator password must be different.")
        _write_private_file(self._password_file, (new_password + "\n").encode("utf-8"))
        self._password = new_password
        self._signing_key = self._derive_signing_key(new_password)
        return self.authenticate(new_password)

    def _derive_signing_key(self, password: str) -> bytes:
        return hmac.new(self._root_signing_key, b"admin-token:" + password.encode("utf-8"), hashlib.sha256).digest()

    def authenticate(self, password: str) -> str | None:
        if not self.auth_required:
            return None
        if not hmac.compare_digest(password, self._password):
            return None
        expires_at = datetime.now(timezone.utc) + timedelta(hours=12)
        payload = {"exp": int(expires_at.timestamp()), "scope": "admin"}
        encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = hmac.new(self._signing_key, encoded.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded}.{_b64encode(signature)}"

    def validate(self, token: str | None) -> bool:
        if not self.auth_required:
            return True
        if not token or "." not in token:
            return False
        encoded, supplied_signature = token.rsplit(".", 1)
        expected = hmac.new(self._signing_key, encoded.encode("ascii"), hashlib.sha256).digest()
        try:
            signature = _b64decode(supplied_signature)
            payload = json.loads(_b64decode(encoded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return hmac.compare_digest(signature, expected) and payload.get("scope") == "admin" and int(
            payload.get("exp", 0)
        ) > int(datetime.now(timezone.utc).timestamp())


def _write_private_file(path: Path, value: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(value)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
