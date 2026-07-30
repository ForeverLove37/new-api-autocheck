"""Request and response contracts for the REST API."""

from __future__ import annotations

from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


ProxyScheme = Literal["http", "https", "socks5"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(StrictModel):
    password: str = Field(min_length=1, max_length=512)


class LoginResponse(StrictModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class AuthStatus(StrictModel):
    auth_required: bool


class ProxyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    scheme: ProxyScheme = "http"
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str | None = Field(default=None, max_length=512)
    password: str | None = Field(default=None, max_length=512)
    enabled: bool = True


class ProxyUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    scheme: ProxyScheme | None = None
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=512)
    password: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None


class ProxyResponse(StrictModel):
    id: int
    name: str
    scheme: ProxyScheme
    host: str
    port: int
    has_auth: bool
    enabled: bool
    assigned_count: int
    created_at: str
    updated_at: str


class AccountCreate(StrictModel):
    label: str | None = Field(default=None, max_length=120)
    username: str = Field(min_length=1, max_length=320)
    password: str | None = Field(default=None, max_length=1024)
    cookie: str | None = Field(default=None, max_length=16_000)
    user_id: str | None = Field(default=None, max_length=120)
    proxy_id: int | None = None
    enabled: bool = True


class AccountUpdate(StrictModel):
    label: str | None = Field(default=None, max_length=120)
    username: str | None = Field(default=None, min_length=1, max_length=320)
    password: str | None = Field(default=None, max_length=1024)
    cookie: str | None = Field(default=None, max_length=16_000)
    user_id: str | None = Field(default=None, max_length=120)
    proxy_id: int | None = None
    enabled: bool | None = None


class ProxyBrief(StrictModel):
    id: int
    name: str
    scheme: ProxyScheme
    host: str
    port: int
    enabled: bool


class AccountResponse(StrictModel):
    id: int
    label: str | None
    username: str
    user_id: str | None
    proxy: ProxyBrief | None
    enabled: bool
    has_password: bool
    has_cookie: bool
    last_login_at: str | None
    last_login_status: str | None
    last_checkin_at: str | None
    last_checkin_status: str | None
    last_checkin_message: str | None
    created_at: str
    updated_at: str


class ProxyAssignmentRequest(StrictModel):
    account_ids: list[int] = Field(min_length=1, max_length=1_000)
    proxy_id: int | None = None


class ProxyAssignmentResponse(StrictModel):
    updated_count: int


class SiteConfig(StrictModel):
    base_url: str = Field(min_length=8, max_length=2_000)
    login_path: str = Field(min_length=1, max_length=2_000)
    checkin_path: str = Field(min_length=1, max_length=2_000)
    referer_path: str | None = Field(default=None, max_length=2_000)
    username_selector: str = Field(min_length=1, max_length=1_000)
    password_selector: str = Field(min_length=1, max_length=1_000)
    submit_selector: str | None = Field(default=None, max_length=1_000)
    post_login_path: str | None = Field(default=None, max_length=2_000)
    custom_headers: dict[str, str] = Field(default_factory=dict)
    schedule_enabled: bool = False
    schedule_hour: int = Field(default=8, ge=0, le=23)
    schedule_minute: int = Field(default=0, ge=0, le=59)
    schedule_timezone: str = Field(default="UTC", min_length=1, max_length=80)
    request_timeout_seconds: int = Field(default=20, ge=3, le=120)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value.rstrip("/")

    @field_validator("schedule_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("schedule_timezone must be a valid IANA timezone") from exc
        return value


class CheckinRunRequest(StrictModel):
    account_ids: list[int] | None = Field(default=None, max_length=1_000)
    enabled_accounts_only: bool = True
    refresh_cookies: bool = False


class ActionResult(StrictModel):
    account_id: int
    action: Literal["login", "checkin"]
    success: bool
    status_code: int | None = None
    message: str
    timestamp: str


class BatchCheckinResponse(StrictModel):
    results: list[ActionResult]


class LogResponse(StrictModel):
    id: int
    account_id: int | None
    account_username: str | None
    action: str
    success: bool
    status_code: int | None
    message: str
    response_excerpt: str | None
    created_at: str


class LegacyImportRequest(StrictModel):
    import_accounts: bool = True
    import_proxies: bool = True


class LegacyImportResponse(StrictModel):
    accounts_created: int
    accounts_updated: int
    proxies_created: int
    proxies_updated: int
    warnings: list[str]


class SummaryResponse(StrictModel):
    accounts_total: int
    accounts_enabled: int
    accounts_with_cookie: int
    proxies_total: int
    last_checkin_at: str | None
    recent_failures: int


class MessageResponse(StrictModel):
    message: str


class JsonPayload(StrictModel):
    payload: dict[str, Any]
