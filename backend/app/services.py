"""Login, check-in, and recurring automation services."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from checkin import perform_checkin
from get_cookie import retrieve_session_cookie

from backend.app.repository import NotFoundError, Repository


class OperationInProgressError(Exception):
    pass


class CheckinService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self._batch_lock = threading.Lock()

    def login(self, account_id: int) -> dict[str, Any]:
        account = self.repository.get_account_secrets(account_id)
        timestamp = _timestamp()
        if not account.get("password"):
            message = "No password is stored for this account. Add one before refreshing its session."
            self.repository.set_login_status(account_id, False)
            self.repository.record_log(account_id=account_id, action="login", success=False, message=message)
            return _action(account_id, "login", False, message, timestamp)

        config = self.repository.get_site_config()
        proxy = self.repository.get_proxy_secrets(account.get("proxy_id"))
        result = retrieve_session_cookie(
            base_url=config["base_url"],
            login_path=config["login_path"],
            username=account["username"],
            password=account["password"],
            username_selector=config["username_selector"],
            password_selector=config["password_selector"],
            submit_selector=config.get("submit_selector"),
            post_login_path=config.get("post_login_path"),
            proxy=proxy,
            timeout_ms=config["request_timeout_seconds"] * 1_000,
        )
        self.repository.set_login_status(account_id, result.success)
        if result.success and result.cookie:
            self.repository.set_cookie(account_id, result.cookie)
            if result.user_id:
                self.repository.set_user_id(account_id, result.user_id)
        self.repository.record_log(
            account_id=account_id,
            action="login",
            success=result.success,
            message=result.message,
        )
        return _action(account_id, "login", result.success, result.message, timestamp)

    def checkin(self, account_id: int, *, refresh_cookie: bool = False) -> dict[str, Any]:
        if refresh_cookie:
            login_result = self.login(account_id)
            if not login_result["success"]:
                message = f"Check-in skipped because session refresh failed: {login_result['message']}"
                self.repository.set_checkin_status(account_id, False, message, _timestamp())
                self.repository.record_log(account_id=account_id, action="checkin", success=False, message=message)
                return _action(account_id, "checkin", False, message, _timestamp())

        account = self.repository.get_account_secrets(account_id)
        timestamp = _timestamp()
        if not account.get("cookie"):
            message = "No session cookie is stored. Refresh the session before checking in."
            self.repository.set_checkin_status(account_id, False, message, timestamp)
            self.repository.record_log(account_id=account_id, action="checkin", success=False, message=message)
            return _action(account_id, "checkin", False, message, timestamp)

        config = self.repository.get_site_config()
        proxy = self.repository.get_proxy_secrets(account.get("proxy_id"))
        result = perform_checkin(
            base_url=config["base_url"],
            checkin_path=config["checkin_path"],
            cookie=account["cookie"],
            user_id=account.get("user_id"),
            referer_path=config.get("referer_path"),
            proxy_url=_request_proxy_url(proxy),
            extra_headers=config.get("custom_headers", {}),
            timeout_seconds=config["request_timeout_seconds"],
        )
        self.repository.set_checkin_status(account_id, result.success, result.message, result.checked_at)
        self.repository.record_log(
            account_id=account_id,
            action="checkin",
            success=result.success,
            status_code=result.status_code,
            message=result.message,
            # Some APIs include account metadata in their response. The UI only
            # needs the status/message, so do not persist raw server payloads.
            response=None,
            created_at=result.checked_at,
        )
        return _action(account_id, "checkin", result.success, result.message, result.checked_at, result.status_code)

    def run_batch(
        self,
        account_ids: list[int] | None,
        *,
        enabled_only: bool,
        refresh_cookies: bool,
    ) -> list[dict[str, Any]]:
        if not self._batch_lock.acquire(blocking=False):
            raise OperationInProgressError("A batch check-in is already running.")
        try:
            selected = self.repository.list_checkin_accounts(account_ids, enabled_only)
            return [self.checkin(account_id, refresh_cookie=refresh_cookies) for account_id in selected]
        finally:
            self._batch_lock.release()


class DailyCheckinScheduler:
    """In-process daily job. Its config is stored through the Site Config API."""

    job_id = "daily-checkin"

    def __init__(self, service: CheckinService, repository: Repository) -> None:
        self.service = service
        self.repository = repository
        self.scheduler = BackgroundScheduler()

    def start(self) -> None:
        self.scheduler.start()
        self.refresh()

    def refresh(self) -> None:
        config = self.repository.get_site_config()
        if self.scheduler.get_job(self.job_id):
            self.scheduler.remove_job(self.job_id)
        if not config["schedule_enabled"]:
            return
        trigger = CronTrigger(
            hour=config["schedule_hour"],
            minute=config["schedule_minute"],
            timezone=config["schedule_timezone"],
        )
        self.scheduler.add_job(
            self._run_scheduled,
            trigger=trigger,
            id=self.job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3_600,
        )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _run_scheduled(self) -> None:
        try:
            self.service.run_batch(None, enabled_only=True, refresh_cookies=False)
        except OperationInProgressError:
            return
        except Exception as exc:
            # The scheduler has no HTTP response channel. This leaves a useful
            # record in the service journal without exposing secrets.
            print(f"Scheduled check-in failed: {exc}")


def _request_proxy_url(proxy: dict[str, Any] | None) -> str | None:
    if not proxy:
        return None
    credentials = ""
    if proxy.get("username"):
        credentials = quote(str(proxy["username"]), safe="")
        if proxy.get("password") is not None:
            credentials += ":" + quote(str(proxy["password"]), safe="")
        credentials += "@"
    return f"{proxy['scheme']}://{credentials}{proxy['host']}:{proxy['port']}"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _action(
    account_id: int,
    action: str,
    success: bool,
    message: str,
    timestamp: str,
    status_code: int | None = None,
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "action": action,
        "success": success,
        "status_code": status_code,
        "message": message,
        "timestamp": timestamp,
    }
