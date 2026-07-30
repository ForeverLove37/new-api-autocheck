"""Reusable Playwright-based session cookie retrieval client."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin, urlparse


@dataclass(slots=True)
class LoginResult:
    success: bool
    cookie: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_url(base_url: str, path_or_url: str) -> str:
    parsed = urlparse(path_or_url)
    if parsed.scheme and parsed.netloc:
        return path_or_url
    return urljoin(base_url.rstrip("/") + "/", path_or_url.lstrip("/"))


def _playwright_proxy(proxy: dict[str, Any] | None) -> dict[str, str] | None:
    if not proxy:
        return None
    server = f"{proxy['scheme']}://{proxy['host']}:{proxy['port']}"
    result = {"server": server}
    if proxy.get("username"):
        result["username"] = str(proxy["username"])
    if proxy.get("password"):
        result["password"] = str(proxy["password"])
    return result


def retrieve_session_cookie(
    *,
    base_url: str,
    login_path: str,
    username: str,
    password: str,
    username_selector: str = 'input[type="email"], input[type="text"]',
    password_selector: str = 'input[type="password"]',
    submit_selector: str | None = None,
    post_login_path: str | None = None,
    proxy: dict[str, Any] | None = None,
    timeout_ms: int = 30_000,
) -> LoginResult:
    """Log in through a browser and return all session cookies as one header.

    The selectors are configuration values, so sites with nonstandard login
    forms can be supported without changing application code.
    """
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return LoginResult(
            success=False,
            cookie=None,
            message="Playwright is not installed. Install requirements and run playwright install chromium.",
        )

    login_url = _resolve_url(base_url, login_path)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                proxy=_playwright_proxy(proxy),
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            try:
                context = browser.new_context()
                page = context.new_page()
                page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_ms)

                login_response_seen = False
                login_response_succeeded = False

                def observe_login_response(response) -> None:
                    nonlocal login_response_seen, login_response_succeeded
                    if response.request.method != "POST" or "login" not in response.url.lower():
                        return
                    login_response_seen = True
                    try:
                        payload = response.json()
                    except Exception:
                        return
                    if isinstance(payload, dict) and payload.get("success") is True:
                        login_response_succeeded = True

                page.on("response", observe_login_response)

                page.locator(username_selector).first.fill(username, timeout=timeout_ms)
                password_input = page.locator(password_selector).first
                password_input.fill(password, timeout=timeout_ms)
                if submit_selector:
                    page.locator(submit_selector).first.click(timeout=timeout_ms)
                else:
                    password_input.press("Enter")

                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 12_000))
                except PlaywrightTimeoutError:
                    # Many dashboards keep polling after successful login.
                    page.wait_for_timeout(1_500)

                if post_login_path:
                    page.goto(
                        _resolve_url(base_url, post_login_path),
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )

                cookies = context.cookies([base_url])
                cookie = "; ".join(f"{item['name']}={item['value']}" for item in cookies)
                if login_response_seen and not login_response_succeeded:
                    return LoginResult(False, None, "The login endpoint did not report a successful response.")
                if cookie:
                    return LoginResult(True, cookie, "Login succeeded and session cookie was refreshed.")
                return LoginResult(False, None, "Login completed but the browser did not receive a session cookie.")
            finally:
                browser.close()
    except Exception as exc:
        return LoginResult(False, None, f"Login error: {exc}")


if __name__ == "__main__":
    raise SystemExit("Use the web application or import retrieve_session_cookie() from this module.")
