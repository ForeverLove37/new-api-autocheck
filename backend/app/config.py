"""Configuration values that are intentionally kept outside the database."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class AppSettings:
    database_path: Path
    data_dir: Path
    legacy_account_file: Path
    legacy_proxy_file: Path
    admin_password: str | None
    encryption_key: str | None
    auth_required: bool

    @classmethod
    def from_environment(cls) -> "AppSettings":
        data_dir = Path(os.getenv("AUTOCHECK_DATA_DIR", str(ROOT_DIR / "data")))
        database_path = Path(os.getenv("AUTOCHECK_DATABASE_PATH", str(data_dir / "autocheck.db")))
        return cls(
            database_path=database_path,
            data_dir=data_dir,
            legacy_account_file=Path(
                os.getenv("AUTOCHECK_LEGACY_ACCOUNT_FILE", str(ROOT_DIR / "account.txt"))
            ),
            legacy_proxy_file=Path(
                os.getenv("AUTOCHECK_LEGACY_PROXY_FILE", str(ROOT_DIR / "proxy.txt"))
            ),
            admin_password=os.getenv("AUTOCHECK_ADMIN_PASSWORD"),
            encryption_key=os.getenv("AUTOCHECK_ENCRYPTION_KEY"),
            auth_required=_truthy(os.getenv("AUTOCHECK_AUTH_REQUIRED"), default=True),
        )
