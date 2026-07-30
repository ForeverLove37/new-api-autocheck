"""One-time migration helpers for the supplied account.txt and proxy.txt files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from backend.app.repository import Repository


ACCOUNT_RE = re.compile(r"^account\s*:\s*(?P<username>.+?)(?:\s*\((?P<label>[^)]*)\))?\s*$", re.I)
PASSWORD_RE = re.compile(r"^password\s*:\s*(?P<password>.+)\s*$", re.I)


@dataclass(slots=True)
class ImportStats:
    accounts_created: int = 0
    accounts_updated: int = 0
    proxies_created: int = 0
    proxies_updated: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, int | list[str]]:
        return {
            "accounts_created": self.accounts_created,
            "accounts_updated": self.accounts_updated,
            "proxies_created": self.proxies_created,
            "proxies_updated": self.proxies_updated,
            "warnings": self.warnings,
        }


def import_legacy_files(
    repository: Repository,
    *,
    account_file: Path,
    proxy_file: Path,
    import_accounts: bool = True,
    import_proxies: bool = True,
) -> ImportStats:
    stats = ImportStats()
    if import_accounts:
        if account_file.exists():
            for line_number, record in _parse_accounts(account_file, stats.warnings):
                created = repository.upsert_legacy_account(**record)
                if created:
                    stats.accounts_created += 1
                else:
                    stats.accounts_updated += 1
        else:
            stats.warnings.append(f"Account import file was not found: {account_file.name}")

    if import_proxies:
        if proxy_file.exists():
            for line_number, record in _parse_proxies(proxy_file, stats.warnings):
                created = repository.upsert_legacy_proxy(record)
                if created:
                    stats.proxies_created += 1
                else:
                    stats.proxies_updated += 1
        else:
            stats.warnings.append(f"Proxy import file was not found: {proxy_file.name}")
    return stats


def _parse_accounts(path: Path, warnings: list[str]):
    pending: dict[str, str | None] | None = None
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        account_match = ACCOUNT_RE.match(line)
        if account_match:
            if pending is not None:
                warnings.append(f"Account line {line_number - 1} has no matching password and was skipped.")
            pending = {
                "username": account_match.group("username").strip(),
                "label": (account_match.group("label") or "").strip() or None,
            }
            continue
        password_match = PASSWORD_RE.match(line)
        if password_match and pending is not None:
            password = password_match.group("password").strip()
            if password:
                yield line_number, {
                    "username": str(pending["username"]),
                    "password": password,
                    "label": pending["label"],
                }
            else:
                warnings.append(f"Account password at line {line_number} is empty and was skipped.")
            pending = None
    if pending is not None:
        warnings.append("The final account entry has no matching password and was skipped.")


def _parse_proxies(path: Path, warnings: list[str]):
    record_number = 0
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.upper().startswith("IP:PORT"):
            continue
        try:
            record = _parse_proxy_line(line, record_number + 1)
        except ValueError:
            warnings.append(f"Proxy line {line_number} could not be parsed and was skipped.")
            continue
        record_number += 1
        yield line_number, record


def _parse_proxy_line(value: str, number: int) -> dict[str, str | int | None]:
    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname or not parsed.port:
            raise ValueError("Invalid proxy URL")
        return {
            "name": f"Imported proxy {number}",
            "scheme": parsed.scheme,
            "host": parsed.hostname,
            "port": parsed.port,
            "username": unquote(parsed.username) if parsed.username else None,
            "password": unquote(parsed.password) if parsed.password else None,
        }

    pieces = value.split(":", 3)
    if len(pieces) not in {2, 4}:
        raise ValueError("Expected host:port or host:port:user:password")
    host, port_text = pieces[0].strip(), pieces[1].strip()
    if not host:
        raise ValueError("Missing host")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("Invalid port") from exc
    if not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    username = pieces[2].strip() if len(pieces) == 4 else None
    password = pieces[3].strip() if len(pieces) == 4 else None
    return {
        "name": f"Imported proxy {number}",
        "scheme": "http",
        "host": host,
        "port": port,
        "username": username or None,
        "password": password or None,
    }
