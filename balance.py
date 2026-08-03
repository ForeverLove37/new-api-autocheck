"""Reusable authenticated balance client for New API-compatible sites."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

from checkin import resolve_url


@dataclass(slots=True)
class BalanceResult:
    success: bool
    status_code: int | None
    message: str
    quota: float | None
    balance: float | None
    display: str | None
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_balance(
    *,
    base_url: str,
    balance_path: str,
    status_path: str,
    cookie: str,
    user_id: str | None = None,
    referer_path: str | None = None,
    proxy_url: str | None = None,
    extra_headers: Mapping[str, str] | None = None,
    timeout_seconds: int = 20,
) -> BalanceResult:
    """Fetch an account quota and convert it using the target's public metadata."""
    headers = {
        "accept": "application/json, text/plain, */*",
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
        response = requests.get(
            resolve_url(base_url, balance_path),
            headers=headers,
            proxies=proxies,
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        return _failed(None, f"Balance request failed ({type(exc).__name__}).", checked_at)

    payload = _json_object(response)
    if payload is None:
        return _failed(response.status_code, "Balance endpoint returned an invalid response.", checked_at)
    if not response.ok or payload.get("success") is False:
        message = str(payload.get("message") or payload.get("error") or "Balance request failed.")
        return _failed(response.status_code, message, checked_at)

    data = payload.get("data")
    if not isinstance(data, dict):
        return _failed(response.status_code, "Balance response did not contain account data.", checked_at)
    quota = _number(data.get("quota"))
    if quota is None:
        return _failed(response.status_code, "Balance response did not contain a numeric quota.", checked_at)

    metadata: dict[str, Any] = {}
    try:
        status_response = requests.get(
            resolve_url(base_url, status_path),
            headers=headers,
            proxies=proxies,
            timeout=timeout_seconds,
        )
        status_payload = _json_object(status_response)
        if status_response.ok and status_payload and isinstance(status_payload.get("data"), dict):
            metadata = status_payload["data"]
    except requests.RequestException:
        # A raw quota is still useful if optional display metadata is unavailable.
        pass

    balance, display = _format_balance(quota, metadata)
    return BalanceResult(
        success=True,
        status_code=response.status_code,
        message=f"Balance: {display}",
        quota=quota,
        balance=balance,
        display=display,
        checked_at=checked_at,
    )


def _json_object(response: requests.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _format_balance(quota: float, metadata: Mapping[str, Any]) -> tuple[float | None, str]:
    quota_per_unit = _number(metadata.get("quota_per_unit"))
    display_in_currency = metadata.get("display_in_currency") is True
    if not display_in_currency or quota_per_unit is None or quota_per_unit <= 0:
        return None, f"{quota:,.0f} quota"

    balance = quota / quota_per_unit
    display_type = str(metadata.get("quota_display_type") or "").upper()
    if display_type == "USD":
        symbol = "$"
    elif display_type == "CNY":
        symbol = "\u00a5"
    elif display_type == "CUSTOM":
        symbol = str(metadata.get("custom_currency_symbol") or "")[:8]
        exchange_rate = _number(metadata.get("custom_currency_exchange_rate"))
        if exchange_rate is not None and exchange_rate > 0:
            balance *= exchange_rate
    else:
        symbol = str(metadata.get("custom_currency_symbol") or "")[:8]
    return balance, f"{symbol}{balance:,.2f}"


def _failed(status_code: int | None, message: str, checked_at: str) -> BalanceResult:
    return BalanceResult(
        success=False,
        status_code=status_code,
        message=message,
        quota=None,
        balance=None,
        display=None,
        checked_at=checked_at,
    )


if __name__ == "__main__":
    raise SystemExit("Use the web application or import fetch_balance() from this module.")
