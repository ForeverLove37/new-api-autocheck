"""SQLite connection and schema management."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ACCOUNT_SCHEDULE_COLUMNS = {
    "schedule_enabled": "INTEGER NOT NULL DEFAULT 0",
    "schedule_hour": "INTEGER NOT NULL DEFAULT 8",
    "schedule_minute": "INTEGER NOT NULL DEFAULT 0",
    "schedule_timezone": "TEXT NOT NULL DEFAULT 'UTC'",
    "schedule_jitter_minutes": "INTEGER NOT NULL DEFAULT 0",
}

ACCOUNT_BALANCE_COLUMNS = {
    "last_balance_quota": "REAL",
    "last_balance_amount": "REAL",
    "last_balance_display": "TEXT",
    "last_balance_at": "TEXT",
    "last_balance_status": "TEXT",
    "last_balance_message": "TEXT",
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS proxies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    scheme TEXT NOT NULL CHECK (scheme IN ('http', 'https', 'socks5')),
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    username_ciphertext TEXT,
                    password_ciphertext TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scheme, host, port)
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT,
                    username TEXT NOT NULL UNIQUE,
                    password_ciphertext TEXT,
                    cookie_ciphertext TEXT,
                    user_id TEXT,
                    proxy_id INTEGER REFERENCES proxies(id) ON DELETE SET NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    schedule_enabled INTEGER NOT NULL DEFAULT 0,
                    schedule_hour INTEGER NOT NULL DEFAULT 8,
                    schedule_minute INTEGER NOT NULL DEFAULT 0,
                    schedule_timezone TEXT NOT NULL DEFAULT 'UTC',
                    schedule_jitter_minutes INTEGER NOT NULL DEFAULT 0,
                    last_login_at TEXT,
                    last_login_status TEXT,
                    last_checkin_at TEXT,
                    last_checkin_status TEXT,
                    last_checkin_message TEXT,
                    last_balance_quota REAL,
                    last_balance_amount REAL,
                    last_balance_display TEXT,
                    last_balance_at TEXT,
                    last_balance_status TEXT,
                    last_balance_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS site_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS checkin_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    status_code INTEGER,
                    message TEXT NOT NULL,
                    response_excerpt TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_accounts_proxy_id ON accounts(proxy_id);
                CREATE INDEX IF NOT EXISTS idx_logs_created_at ON checkin_logs(created_at DESC);
                """
            )
            existing_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(accounts)").fetchall()
            }
            for column, declaration in {**ACCOUNT_SCHEDULE_COLUMNS, **ACCOUNT_BALANCE_COLUMNS}.items():
                if column not in existing_columns:
                    connection.execute(f"ALTER TABLE accounts ADD COLUMN {column} {declaration}")
