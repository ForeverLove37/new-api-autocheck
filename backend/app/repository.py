"""Persistence operations and secret-safe response projection."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from backend.app.crypto import SecretBox
from backend.app.database import Database


DEFAULT_SITE_CONFIG: dict[str, Any] = {
    "base_url": "https://liangjiewis.com",
    "login_path": "/login",
    "checkin_path": "/api/user/checkin",
    "referer_path": "/console/personal",
    "username_selector": 'input[type="email"], input[type="text"]',
    "password_selector": 'input[type="password"]',
    "submit_selector": None,
    "post_login_path": "/console/personal",
    "custom_headers": {},
    "schedule_enabled": False,
    "schedule_hour": 8,
    "schedule_minute": 0,
    "schedule_timezone": "UTC",
    # Login pages can be temporarily slow while the target is under load.
    # Keep this below the API schema maximum while allowing Playwright enough
    # time to establish an authenticated browser session.
    "request_timeout_seconds": 60,
}


class NotFoundError(Exception):
    pass


class Repository:
    def __init__(self, database: Database, secrets: SecretBox) -> None:
        self.database = database
        self.secrets = secrets

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def count_accounts(self) -> int:
        with self.database.connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])

    def count_proxies(self) -> int:
        with self.database.connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM proxies").fetchone()[0])

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(self._accounts_query() + " ORDER BY a.id ASC").fetchall()
        return [self._account_public(row) for row in rows]

    def get_account(self, account_id: int) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(self._accounts_query() + " WHERE a.id = ?", (account_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Account {account_id} was not found.")
        return self._account_public(row)

    def get_account_secrets(self, account_id: int) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Account {account_id} was not found.")
        result = dict(row)
        result["password"] = self.secrets.decrypt(result.pop("password_ciphertext"))
        result["cookie"] = self.secrets.decrypt(result.pop("cookie_ciphertext"))
        return result

    def create_account(self, values: dict[str, Any]) -> dict[str, Any]:
        self._ensure_proxy_exists(values.get("proxy_id"))
        now = self._now()
        try:
            with self.database.connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO accounts (
                        label, username, password_ciphertext, cookie_ciphertext, user_id, proxy_id,
                        enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._none_if_empty(values.get("label")),
                        values["username"],
                        self.secrets.encrypt(self._none_if_empty(values.get("password"))),
                        self.secrets.encrypt(self._none_if_empty(values.get("cookie"))),
                        self._none_if_empty(values.get("user_id")),
                        values.get("proxy_id"),
                        int(values.get("enabled", True)),
                        now,
                        now,
                    ),
                )
                account_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("An account with this username already exists.") from exc
        return self.get_account(account_id)

    def update_account(self, account_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        self.get_account(account_id)
        if "proxy_id" in changes:
            self._ensure_proxy_exists(changes["proxy_id"])
        mapped: dict[str, Any] = {}
        direct_fields = {"label", "username", "user_id", "proxy_id", "enabled"}
        for key in direct_fields.intersection(changes):
            value = changes[key]
            if key in {"label", "user_id"}:
                value = self._none_if_empty(value)
            if key == "enabled" and value is not None:
                value = int(value)
            mapped[key] = value
        if "password" in changes:
            mapped["password_ciphertext"] = self.secrets.encrypt(self._none_if_empty(changes["password"]))
        if "cookie" in changes:
            mapped["cookie_ciphertext"] = self.secrets.encrypt(self._none_if_empty(changes["cookie"]))
        if not mapped:
            return self.get_account(account_id)
        mapped["updated_at"] = self._now()
        assignment = ", ".join(f"{column} = ?" for column in mapped)
        try:
            with self.database.connection() as connection:
                connection.execute(
                    f"UPDATE accounts SET {assignment} WHERE id = ?", (*mapped.values(), account_id)
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("An account with this username already exists.") from exc
        return self.get_account(account_id)

    def delete_account(self, account_id: int) -> None:
        with self.database.connection() as connection:
            cursor = connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        if cursor.rowcount == 0:
            raise NotFoundError(f"Account {account_id} was not found.")

    def list_proxies(self) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT p.*, COUNT(a.id) AS assigned_count
                FROM proxies AS p
                LEFT JOIN accounts AS a ON a.proxy_id = p.id
                GROUP BY p.id
                ORDER BY p.id ASC
                """
            ).fetchall()
        return [self._proxy_public(row) for row in rows]

    def get_proxy(self, proxy_id: int) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT p.*, COUNT(a.id) AS assigned_count
                FROM proxies AS p
                LEFT JOIN accounts AS a ON a.proxy_id = p.id
                WHERE p.id = ?
                GROUP BY p.id
                """,
                (proxy_id,),
            ).fetchone()
        if not row:
            raise NotFoundError(f"Proxy {proxy_id} was not found.")
        return self._proxy_public(row)

    def get_proxy_secrets(self, proxy_id: int | None) -> dict[str, Any] | None:
        if proxy_id is None:
            return None
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM proxies WHERE id = ?", (proxy_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Proxy {proxy_id} was not found.")
        result = dict(row)
        if not result["enabled"]:
            return None
        result["username"] = self.secrets.decrypt(result.pop("username_ciphertext"))
        result["password"] = self.secrets.decrypt(result.pop("password_ciphertext"))
        return result

    def create_proxy(self, values: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        try:
            with self.database.connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO proxies (
                        name, scheme, host, port, username_ciphertext, password_ciphertext,
                        enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        values["name"],
                        values["scheme"],
                        values["host"],
                        values["port"],
                        self.secrets.encrypt(self._none_if_empty(values.get("username"))),
                        self.secrets.encrypt(self._none_if_empty(values.get("password"))),
                        int(values.get("enabled", True)),
                        now,
                        now,
                    ),
                )
                proxy_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("A proxy with this scheme, host, and port already exists.") from exc
        return self.get_proxy(proxy_id)

    def update_proxy(self, proxy_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        self.get_proxy(proxy_id)
        mapped: dict[str, Any] = {}
        for key in {"name", "scheme", "host", "port", "enabled"}.intersection(changes):
            value = changes[key]
            mapped[key] = int(value) if key == "enabled" and value is not None else value
        if "username" in changes:
            mapped["username_ciphertext"] = self.secrets.encrypt(self._none_if_empty(changes["username"]))
        if "password" in changes:
            mapped["password_ciphertext"] = self.secrets.encrypt(self._none_if_empty(changes["password"]))
        if not mapped:
            return self.get_proxy(proxy_id)
        mapped["updated_at"] = self._now()
        assignment = ", ".join(f"{column} = ?" for column in mapped)
        try:
            with self.database.connection() as connection:
                connection.execute(f"UPDATE proxies SET {assignment} WHERE id = ?", (*mapped.values(), proxy_id))
        except sqlite3.IntegrityError as exc:
            raise ValueError("A proxy with this scheme, host, and port already exists.") from exc
        return self.get_proxy(proxy_id)

    def delete_proxy(self, proxy_id: int) -> None:
        with self.database.connection() as connection:
            cursor = connection.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))
        if cursor.rowcount == 0:
            raise NotFoundError(f"Proxy {proxy_id} was not found.")

    def assign_proxy(self, account_ids: list[int], proxy_id: int | None) -> int:
        self._ensure_proxy_exists(proxy_id)
        unique_ids = sorted(set(account_ids))
        placeholders = ",".join("?" for _ in unique_ids)
        with self.database.connection() as connection:
            existing = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM accounts WHERE id IN ({placeholders})", unique_ids
                ).fetchone()[0]
            )
            if existing != len(unique_ids):
                raise NotFoundError("One or more selected accounts were not found.")
            cursor = connection.execute(
                f"UPDATE accounts SET proxy_id = ?, updated_at = ? WHERE id IN ({placeholders})",
                (proxy_id, self._now(), *unique_ids),
            )
        return cursor.rowcount

    def get_site_config(self) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute("SELECT value FROM site_settings WHERE key = 'site_config'").fetchone()
        if not row:
            return dict(DEFAULT_SITE_CONFIG)
        stored = json.loads(row["value"])
        return {**DEFAULT_SITE_CONFIG, **stored}

    def save_site_config(self, values: dict[str, Any]) -> dict[str, Any]:
        config = {**DEFAULT_SITE_CONFIG, **values}
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO site_settings (key, value, updated_at) VALUES ('site_config', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (json.dumps(config, separators=(",", ":")), self._now()),
            )
        return config

    def set_login_status(self, account_id: int, success: bool) -> None:
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE accounts SET last_login_at = ?, last_login_status = ?, updated_at = ? WHERE id = ?",
                (self._now(), "success" if success else "failed", self._now(), account_id),
            )

    def set_cookie(self, account_id: int, cookie: str) -> None:
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE accounts SET cookie_ciphertext = ?, updated_at = ? WHERE id = ?",
                (self.secrets.encrypt(cookie), self._now(), account_id),
            )

    def set_user_id(self, account_id: int, user_id: str) -> None:
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE accounts SET user_id = ?, updated_at = ? WHERE id = ?",
                (user_id, self._now(), account_id),
            )

    def set_checkin_status(self, account_id: int, success: bool, message: str, timestamp: str) -> None:
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE accounts
                SET last_checkin_at = ?, last_checkin_status = ?, last_checkin_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (timestamp, "success" if success else "failed", message[:1_000], self._now(), account_id),
            )

    def record_log(
        self,
        *,
        account_id: int | None,
        action: str,
        success: bool,
        message: str,
        status_code: int | None = None,
        response: Any = None,
        created_at: str | None = None,
    ) -> None:
        excerpt = None
        if response is not None:
            excerpt = json.dumps(response, ensure_ascii=False) if not isinstance(response, str) else response
            excerpt = excerpt[:2_000]
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO checkin_logs (
                    account_id, action, success, status_code, message, response_excerpt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (account_id, action, int(success), status_code, message[:1_000], excerpt, created_at or self._now()),
            )

    def list_logs(self, limit: int) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT l.*, a.username AS account_username
                FROM checkin_logs AS l
                LEFT JOIN accounts AS a ON a.id = l.account_id
                ORDER BY l.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "account_id": row["account_id"],
                "account_username": row["account_username"],
                "action": row["action"],
                "success": bool(row["success"]),
                "status_code": row["status_code"],
                "message": row["message"],
                "response_excerpt": row["response_excerpt"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def summary(self) -> dict[str, Any]:
        with self.database.connection() as connection:
            account_totals = connection.execute(
                """
                SELECT
                    COUNT(*) AS accounts_total,
                    SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS accounts_enabled,
                    SUM(CASE WHEN cookie_ciphertext IS NOT NULL THEN 1 ELSE 0 END) AS accounts_with_cookie,
                    MAX(last_checkin_at) AS last_checkin_at
                FROM accounts
                """
            ).fetchone()
            proxies_total = int(connection.execute("SELECT COUNT(*) FROM proxies").fetchone()[0])
            recent_failures = int(
                connection.execute(
                    "SELECT COUNT(*) FROM checkin_logs WHERE success = 0 AND created_at >= datetime('now', '-1 day')"
                ).fetchone()[0]
            )
        return {
            "accounts_total": int(account_totals["accounts_total"] or 0),
            "accounts_enabled": int(account_totals["accounts_enabled"] or 0),
            "accounts_with_cookie": int(account_totals["accounts_with_cookie"] or 0),
            "proxies_total": proxies_total,
            "last_checkin_at": account_totals["last_checkin_at"],
            "recent_failures": recent_failures,
        }

    def list_checkin_accounts(self, account_ids: list[int] | None, enabled_only: bool) -> list[int]:
        with self.database.connection() as connection:
            if account_ids is not None:
                unique_ids = sorted(set(account_ids))
                if not unique_ids:
                    return []
                placeholders = ",".join("?" for _ in unique_ids)
                rows = connection.execute(
                    f"SELECT id FROM accounts WHERE id IN ({placeholders}) ORDER BY id", unique_ids
                ).fetchall()
                if len(rows) != len(unique_ids):
                    raise NotFoundError("One or more selected accounts were not found.")
            elif enabled_only:
                rows = connection.execute("SELECT id FROM accounts WHERE enabled = 1 ORDER BY id").fetchall()
            else:
                rows = connection.execute("SELECT id FROM accounts ORDER BY id").fetchall()
        return [int(row["id"]) for row in rows]

    def upsert_legacy_account(self, *, username: str, password: str, label: str | None) -> bool:
        now = self._now()
        with self.database.connection() as connection:
            existing = connection.execute("SELECT id FROM accounts WHERE username = ?", (username,)).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE accounts SET label = ?, password_ciphertext = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (label, self.secrets.encrypt(password), now, existing["id"]),
                )
                return False
            connection.execute(
                """
                INSERT INTO accounts (label, username, password_ciphertext, enabled, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (label, username, self.secrets.encrypt(password), now, now),
            )
            return True

    def upsert_legacy_proxy(self, values: dict[str, Any]) -> bool:
        now = self._now()
        with self.database.connection() as connection:
            existing = connection.execute(
                "SELECT id FROM proxies WHERE scheme = ? AND host = ? AND port = ?",
                (values["scheme"], values["host"], values["port"]),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE proxies
                    SET name = ?, username_ciphertext = ?, password_ciphertext = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        values["name"],
                        self.secrets.encrypt(self._none_if_empty(values.get("username"))),
                        self.secrets.encrypt(self._none_if_empty(values.get("password"))),
                        now,
                        existing["id"],
                    ),
                )
                return False
            connection.execute(
                """
                INSERT INTO proxies (
                    name, scheme, host, port, username_ciphertext, password_ciphertext,
                    enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    values["name"],
                    values["scheme"],
                    values["host"],
                    values["port"],
                    self.secrets.encrypt(self._none_if_empty(values.get("username"))),
                    self.secrets.encrypt(self._none_if_empty(values.get("password"))),
                    now,
                    now,
                ),
            )
            return True

    def _ensure_proxy_exists(self, proxy_id: int | None) -> None:
        if proxy_id is None:
            return
        with self.database.connection() as connection:
            exists = connection.execute("SELECT 1 FROM proxies WHERE id = ?", (proxy_id,)).fetchone()
        if not exists:
            raise NotFoundError(f"Proxy {proxy_id} was not found.")

    @staticmethod
    def _none_if_empty(value: Any) -> Any:
        return None if value is None or value == "" else value

    @staticmethod
    def _accounts_query() -> str:
        return """
            SELECT
                a.*,
                p.id AS proxy_row_id,
                p.name AS proxy_name,
                p.scheme AS proxy_scheme,
                p.host AS proxy_host,
                p.port AS proxy_port,
                p.enabled AS proxy_enabled
            FROM accounts AS a
            LEFT JOIN proxies AS p ON p.id = a.proxy_id
        """

    @staticmethod
    def _account_public(row: sqlite3.Row) -> dict[str, Any]:
        proxy = None
        if row["proxy_row_id"] is not None:
            proxy = {
                "id": row["proxy_row_id"],
                "name": row["proxy_name"],
                "scheme": row["proxy_scheme"],
                "host": row["proxy_host"],
                "port": row["proxy_port"],
                "enabled": bool(row["proxy_enabled"]),
            }
        return {
            "id": row["id"],
            "label": row["label"],
            "username": row["username"],
            "user_id": row["user_id"],
            "proxy": proxy,
            "enabled": bool(row["enabled"]),
            "has_password": bool(row["password_ciphertext"]),
            "has_cookie": bool(row["cookie_ciphertext"]),
            "last_login_at": row["last_login_at"],
            "last_login_status": row["last_login_status"],
            "last_checkin_at": row["last_checkin_at"],
            "last_checkin_status": row["last_checkin_status"],
            "last_checkin_message": row["last_checkin_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _proxy_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "scheme": row["scheme"],
            "host": row["host"],
            "port": row["port"],
            "has_auth": bool(row["username_ciphertext"]),
            "enabled": bool(row["enabled"]),
            "assigned_count": int(row["assigned_count"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
