"""Login, check-in, balance, and recurring automation services."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from balance import fetch_balance
from checkin import perform_checkin
from get_cookie import retrieve_session_cookie

from backend.app.repository import NotFoundError, Repository


class OperationInProgressError(Exception):
    pass


class CheckinService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self._batch_lock = threading.Lock()
        self._balance_batch_lock = threading.Lock()

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

    def balance(self, account_id: int, *, refresh_cookie: bool = False) -> dict[str, Any]:
        if refresh_cookie:
            login_result = self.login(account_id)
            if not login_result["success"]:
                timestamp = _timestamp()
                message = f"Balance check skipped because session refresh failed: {login_result['message']}"
                self.repository.set_balance_status(
                    account_id,
                    success=False,
                    message=message,
                    timestamp=timestamp,
                )
                self.repository.record_log(
                    account_id=account_id,
                    action="balance",
                    success=False,
                    message=message,
                    created_at=timestamp,
                )
                return _balance_action(account_id, False, message, timestamp)

        account = self.repository.get_account_secrets(account_id)
        timestamp = _timestamp()
        if not account.get("cookie"):
            message = "No session cookie is stored. Refresh the session before checking the balance."
            self.repository.set_balance_status(
                account_id,
                success=False,
                message=message,
                timestamp=timestamp,
            )
            self.repository.record_log(
                account_id=account_id,
                action="balance",
                success=False,
                message=message,
                created_at=timestamp,
            )
            return _balance_action(account_id, False, message, timestamp)

        config = self.repository.get_site_config()
        proxy = self.repository.get_proxy_secrets(account.get("proxy_id"))
        result = fetch_balance(
            base_url=config["base_url"],
            balance_path=config["balance_path"],
            status_path=config["status_path"],
            cookie=account["cookie"],
            user_id=account.get("user_id"),
            referer_path=config.get("referer_path"),
            proxy_url=_request_proxy_url(proxy),
            extra_headers=config.get("custom_headers", {}),
            timeout_seconds=config["request_timeout_seconds"],
        )
        self.repository.set_balance_status(
            account_id,
            success=result.success,
            message=result.message,
            timestamp=result.checked_at,
            quota=result.quota,
            balance=result.balance,
            display=result.display,
        )
        self.repository.record_log(
            account_id=account_id,
            action="balance",
            success=result.success,
            status_code=result.status_code,
            message=result.message,
            response=None,
            created_at=result.checked_at,
        )
        return _balance_action(
            account_id,
            result.success,
            result.message,
            result.checked_at,
            status_code=result.status_code,
            quota=result.quota,
            balance=result.balance,
            display=result.display,
        )

    def run_balance_batch(
        self,
        account_ids: list[int] | None,
        *,
        enabled_only: bool,
        refresh_cookies: bool,
    ) -> list[dict[str, Any]]:
        if not self._balance_batch_lock.acquire(blocking=False):
            raise OperationInProgressError("A batch balance check is already running.")
        try:
            selected = self.repository.list_checkin_accounts(account_ids, enabled_only)
            return [self.balance(account_id, refresh_cookie=refresh_cookies) for account_id in selected]
        finally:
            self._balance_batch_lock.release()


class DailyCheckinScheduler:
    """In-process daily jobs configured independently for each account."""

    job_prefix = "daily-checkin:"

    def __init__(self, service: CheckinService, repository: Repository) -> None:
        self.service = service
        self.repository = repository
        self.scheduler = BackgroundScheduler()

    def start(self) -> None:
        self.scheduler.start()
        self.refresh()

    def refresh(self, account_id: int | None = None) -> None:
        schedule_timezone = self.repository.get_site_config()["schedule_timezone"]
        if account_id is not None:
            job_id = self._job_id(account_id)
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            account = self.repository.get_scheduled_account(account_id)
            if account:
                self._add_account_job(account, schedule_timezone)
            return

        for job in self.scheduler.get_jobs():
            if job.id.startswith(self.job_prefix):
                self.scheduler.remove_job(job.id)
        for account in self.repository.list_scheduled_accounts():
            self._add_account_job(account, schedule_timezone)

    def next_run_at(self, account_id: int) -> str | None:
        job = self.scheduler.get_job(self._job_id(account_id))
        next_run_time = getattr(job, "next_run_time", None) if job else None
        return next_run_time.astimezone(timezone.utc).isoformat() if next_run_time else None

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _run_scheduled(self, account_id: int) -> None:
        try:
            self.service.checkin(account_id, refresh_cookie=False)
        except Exception as exc:
            # The scheduler has no HTTP response channel. This leaves a useful
            # record in the service journal without exposing secrets.
            print(f"Scheduled check-in failed: {exc}")

    def _add_account_job(self, account: dict[str, Any], schedule_timezone: str) -> None:
        account_id = int(account["id"])
        trigger = CronTrigger(
            hour=account["schedule_hour"],
            minute=account["schedule_minute"],
            timezone=schedule_timezone,
            jitter=account["schedule_jitter_minutes"] * 60,
        )
        self.scheduler.add_job(
            self._run_scheduled,
            trigger=trigger,
            args=[account_id],
            id=self._job_id(account_id),
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3_600,
        )

    def _job_id(self, account_id: int) -> str:
        return f"{self.job_prefix}{account_id}"


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


def _balance_action(
    account_id: int,
    success: bool,
    message: str,
    timestamp: str,
    *,
    status_code: int | None = None,
    quota: float | None = None,
    balance: float | None = None,
    display: str | None = None,
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "action": "balance",
        "success": success,
        "status_code": status_code,
        "message": message,
        "quota": quota,
        "balance": balance,
        "display": display,
        "timestamp": timestamp,
    }
