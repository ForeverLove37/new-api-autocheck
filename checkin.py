"""Reusable HTTP check-in client.

This module deliberately contains no database or web-framework code so it can
also be run independently and is easy to test. The FastAPI service imports
``perform_checkin`` below.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

import requests


@dataclass(slots=True)
class CheckinResult:
    success: bool
    status_code: int | None
    message: str
    response: dict[str, Any] | str | None
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_url(base_url: str, path_or_url: str) -> str:
    """Resolve a configured path, while accepting a complete custom URL."""
    parsed = urlparse(path_or_url)
    if parsed.scheme and parsed.netloc:
        return path_or_url
    return urljoin(base_url.rstrip("/") + "/", path_or_url.lstrip("/"))


def perform_checkin(
    *,
    base_url: str,
    checkin_path: str,
    cookie: str,
    user_id: str | None = None,
    referer_path: str | None = None,
    proxy_url: str | None = None,
    extra_headers: Mapping[str, str] | None = None,
    method: str = "POST",
    timeout_seconds: int = 20,
) -> CheckinResult:
    """Call a site's check-in endpoint and return a serializable result.

    ``proxy_url`` can use ``http://``, ``https://``, or ``socks5://``. SOCKS
    support is supplied by the ``requests[socks]`` dependency.
    """
    target_url = resolve_url(base_url, checkin_path)
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json; charset=utf-8",
        "user-agent": "AutoCheck/1.0 (+self-hosted)",
        "cookie": cookie,
    }
    if referer_path:
        headers["referer"] = resolve_url(base_url, referer_path)
    if user_id:
        headers["new-api-user"] = str(user_id)
    if extra_headers:
        for key, value in extra_headers.items():
            if value is not None:
                headers[str(key)] = str(value).replace("{user_id}", str(user_id or ""))

    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    checked_at = datetime.now(timezone.utc).isoformat()

    try:
        response = requests.request(
            method=method.upper(),
            url=target_url,
            headers=headers,
            json={},
            proxies=proxies,
            timeout=timeout_seconds,
        )
        status_code = response.status_code
        try:
            payload: dict[str, Any] | str | None = response.json()
        except ValueError:
            payload = response.text[:4_000] or None

        if isinstance(payload, dict):
            explicit_success = payload.get("success")
            success = bool(explicit_success) if explicit_success is not None else response.ok
            message = str(payload.get("message") or payload.get("error") or "Request completed")
        else:
            success = response.ok
            message = "Request completed" if response.ok else (response.text[:500] or response.reason)

        return CheckinResult(
            success=success,
            status_code=status_code,
            message=message,
            response=payload,
            checked_at=checked_at,
        )
    except requests.RequestException as exc:
        return CheckinResult(
            success=False,
            status_code=None,
            message=f"Request error: {exc}",
            response=None,
            checked_at=checked_at,
        )


if __name__ == "__main__":
    raise SystemExit("Use the web application or import perform_checkin() from this module.")
