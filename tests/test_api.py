from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from starlette.concurrency import run_in_threadpool

from checkin import CheckinResult
from get_cookie import LoginResult
from backend.app.config import AppSettings
from backend.app.main import create_app


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        database_path=tmp_path / "data" / "autocheck.db",
        data_dir=tmp_path / "data",
        legacy_account_file=tmp_path / "missing-account.txt",
        legacy_proxy_file=tmp_path / "missing-proxy.txt",
        admin_password="test-admin-password",
        encryption_key=Fernet.generate_key().decode("ascii"),
        auth_required=True,
    )


async def request(
    app,
    method: str,
    target: str,
    *,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict | None]:
    parsed = urlsplit(target)
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    raw_headers = [(b"host", b"testserver")]
    if body:
        raw_headers.append((b"content-type", b"application/json"))
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode("ascii"),
        "query_string": parsed.query.encode("ascii"),
        "headers": raw_headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
        "app": app,
    }
    messages: list[dict] = []
    sent_request = False

    async def receive() -> dict:
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(scope, receive, send)
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, json.loads(response_body) if response_body else None


async def authenticate(app) -> dict[str, str]:
    status, response = await request(app, "POST", "/api/auth/login", payload={"password": "test-admin-password"})
    assert status == 200
    return {"Authorization": f"Bearer {response['access_token']}"}


def test_account_proxy_crud_and_bulk_assignment(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(make_settings(tmp_path))
        async with app.router.lifespan_context(app):
            status, _ = await request(app, "GET", "/api/accounts")
            assert status == 401
            headers = await authenticate(app)

            status, proxy = await request(
                app,
                "POST",
                "/api/proxies",
                headers=headers,
                payload={
                    "name": "Test SOCKS",
                    "scheme": "socks5",
                    "host": "127.0.0.1",
                    "port": 1080,
                    "username": "proxy-user",
                    "password": "proxy-password",
                },
            )
            assert status == 201
            assert "password" not in proxy

            status, first = await request(
                app,
                "POST",
                "/api/accounts",
                headers=headers,
                payload={"label": "First", "username": "first@example.test", "password": "secret-one"},
            )
            assert status == 201
            status, second = await request(
                app,
                "POST",
                "/api/accounts",
                headers=headers,
                payload={"username": "second@example.test", "password": "secret-two"},
            )
            assert status == 201
            assert first["has_password"] is True
            assert "password" not in first

            status, assigned = await request(
                app,
                "PATCH",
                "/api/proxies/assignments",
                headers=headers,
                payload={"account_ids": [first["id"], second["id"]], "proxy_id": proxy["id"]},
            )
            assert status == 200
            assert assigned == {"updated_count": 2}

            status, accounts = await request(app, "GET", "/api/accounts", headers=headers)
            assert status == 200
            assert all(account["proxy"]["id"] == proxy["id"] for account in accounts)

    asyncio.run(scenario())


def test_login_and_checkin_use_service_contracts(tmp_path: Path, monkeypatch) -> None:
    def fake_login(**kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AssertionError("Login work must run outside the ASGI event loop")
        assert kwargs["proxy"]["scheme"] == "http"
        return LoginResult(True, "session=refreshed", "Login succeeded")

    def fake_checkin(**kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AssertionError("Check-in work must run outside the ASGI event loop")
        assert kwargs["cookie"] == "session=refreshed"
        assert kwargs["proxy_url"] == "http://proxy-user:proxy-password@127.0.0.1:8080"
        return CheckinResult(True, 200, "Checked in", {"success": True}, "2026-01-01T00:00:00+00:00")

    monkeypatch.setattr("backend.app.services.retrieve_session_cookie", fake_login)
    monkeypatch.setattr("backend.app.services.perform_checkin", fake_checkin)

    async def scenario() -> None:
        app = create_app(make_settings(tmp_path))
        async with app.router.lifespan_context(app):
            repository = app.state.container.repository
            proxy = repository.create_proxy(
                {
                    "name": "HTTP proxy",
                    "scheme": "http",
                    "host": "127.0.0.1",
                    "port": 8080,
                    "username": "proxy-user",
                    "password": "proxy-password",
                }
            )
            account = repository.create_account(
                {
                    "username": "user@example.test",
                    "password": "account-password",
                    "proxy_id": proxy["id"],
                }
            )
            checkins = app.state.container.checkins

            login = await run_in_threadpool(checkins.login, account["id"])
            assert login["success"] is True

            checkin = await run_in_threadpool(checkins.checkin, account["id"])
            assert checkin["status_code"] == 200

            batch = await run_in_threadpool(
                checkins.run_batch,
                [account["id"]],
                enabled_only=False,
                refresh_cookies=True,
            )
            assert batch[0]["success"] is True

            stored = repository.get_account(account["id"])
            assert stored["has_cookie"] is True
            assert stored["last_checkin_status"] == "success"

    asyncio.run(scenario())


def test_legacy_text_migration(tmp_path: Path) -> None:
    account_file = tmp_path / "accounts.txt"
    proxy_file = tmp_path / "proxies.txt"
    account_file.write_text(
        "Acct1:\naccount: person@example.test (Personal)\nPassword: a-password\n",
        encoding="utf-8",
    )
    proxy_file.write_text("IP:PORT:USER:PASS\n127.0.0.1:1080:proxy:pass\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    settings = AppSettings(
        database_path=settings.database_path,
        data_dir=settings.data_dir,
        legacy_account_file=account_file,
        legacy_proxy_file=proxy_file,
        admin_password=settings.admin_password,
        encryption_key=settings.encryption_key,
        auth_required=True,
    )

    async def scenario() -> None:
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            headers = await authenticate(app)
            status, accounts = await request(app, "GET", "/api/accounts", headers=headers)
            assert status == 200
            status, proxies = await request(app, "GET", "/api/proxies", headers=headers)
            assert status == 200
            assert accounts[0]["username"] == "person@example.test"
            assert accounts[0]["has_password"] is True
            assert proxies[0]["scheme"] == "http"
            assert proxies[0]["has_auth"] is True

    asyncio.run(scenario())
