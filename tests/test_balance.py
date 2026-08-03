from __future__ import annotations

from typing import Any

from balance import fetch_balance


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def test_fetch_balance_uses_authenticated_proxy_and_target_metadata(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    responses = iter(
        [
            FakeResponse(200, {"success": True, "data": {"quota": 6_750_000}}),
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": {
                        "display_in_currency": True,
                        "quota_per_unit": 500_000,
                        "quota_display_type": "USD",
                    },
                },
            ),
        ]
    )

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return next(responses)

    monkeypatch.setattr("balance.requests.get", fake_get)

    result = fetch_balance(
        base_url="https://target.example",
        balance_path="/api/user/self",
        status_path="/api/status",
        cookie="session=test",
        user_id="42",
        referer_path="/console/personal",
        proxy_url="socks5://127.0.0.1:1080",
        extra_headers={"x-account": "{user_id}"},
        timeout_seconds=30,
    )

    assert result.success is True
    assert result.quota == 6_750_000
    assert result.balance == 13.5
    assert result.display == "$13.50"
    assert [call["url"] for call in calls] == [
        "https://target.example/api/user/self",
        "https://target.example/api/status",
    ]
    assert calls[0]["headers"]["cookie"] == "session=test"
    assert calls[0]["headers"]["new-api-user"] == "42"
    assert calls[0]["headers"]["x-account"] == "42"
    assert calls[0]["proxies"]["https"] == "socks5://127.0.0.1:1080"


def test_fetch_balance_keeps_raw_quota_when_metadata_is_unavailable(monkeypatch) -> None:
    responses = iter(
        [
            FakeResponse(200, {"success": True, "data": {"quota": 1_234}}),
            FakeResponse(503, {"success": False, "message": "Unavailable"}),
        ]
    )
    monkeypatch.setattr("balance.requests.get", lambda *args, **kwargs: next(responses))

    result = fetch_balance(
        base_url="https://target.example",
        balance_path="/api/user/self",
        status_path="/api/status",
        cookie="session=test",
    )

    assert result.success is True
    assert result.balance is None
    assert result.display == "1,234 quota"
