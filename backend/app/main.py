"""FastAPI entry point for the self-hosted check-in manager."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from backend.app.config import ROOT_DIR, AppSettings
from backend.app.crypto import AuthManager, SecretBox
from backend.app.database import Database
from backend.app.legacy_import import import_legacy_files
from backend.app.repository import NotFoundError, Repository
from backend.app.schemas import (
    AccountCreate,
    AccountResponse,
    AccountUpdate,
    ActionResult,
    AuthStatus,
    BatchCheckinResponse,
    CheckinRunRequest,
    LegacyImportRequest,
    LegacyImportResponse,
    LoginRequest,
    LoginResponse,
    LogResponse,
    MessageResponse,
    ProxyAssignmentRequest,
    ProxyAssignmentResponse,
    ProxyCreate,
    ProxyResponse,
    ProxyUpdate,
    SiteConfig,
    SummaryResponse,
)
from backend.app.services import CheckinService, DailyCheckinScheduler, OperationInProgressError


@dataclass(slots=True)
class Container:
    settings: AppSettings
    repository: Repository
    auth: AuthManager
    checkins: CheckinService
    scheduler: DailyCheckinScheduler


def create_app(settings: AppSettings | None = None) -> FastAPI:
    app_settings = settings or AppSettings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(app_settings.database_path)
        database.initialize()
        secrets = SecretBox(app_settings.data_dir, app_settings.encryption_key)
        repository = Repository(database, secrets)
        auth = AuthManager(
            data_dir=app_settings.data_dir,
            configured_password=app_settings.admin_password,
            signing_key=secrets.signing_key,
            auth_required=app_settings.auth_required,
        )
        checkins = CheckinService(repository)
        scheduler = DailyCheckinScheduler(checkins, repository)
        app.state.container = Container(app_settings, repository, auth, checkins, scheduler)

        # Migration is intentionally idempotent and only happens automatically
        # for an empty table. Operators can explicitly re-run it through the API.
        if repository.count_accounts() == 0 or repository.count_proxies() == 0:
            import_legacy_files(
                repository,
                account_file=app_settings.legacy_account_file,
                proxy_file=app_settings.legacy_proxy_file,
                import_accounts=repository.count_accounts() == 0,
                import_proxies=repository.count_proxies() == 0,
            )
        scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown()

    app = FastAPI(
        title="AutoCheck API",
        version="1.0.0",
        description="Self-hosted account check-in and proxy management API.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> Response:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> Response:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def get_container(request: Request) -> Container:
        return request.app.state.container

    async def require_admin(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        container = get_container(request)
        if not container.auth.auth_required:
            return
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not container.auth.validate(token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    @app.get("/api/health", response_model=MessageResponse)
    async def health() -> MessageResponse:
        return MessageResponse(message="ok")

    @app.get("/api/auth/status", response_model=AuthStatus)
    async def auth_status(request: Request) -> AuthStatus:
        return AuthStatus(auth_required=get_container(request).auth.auth_required)

    @app.post("/api/auth/login", response_model=LoginResponse)
    async def authenticate(payload: LoginRequest, request: Request) -> LoginResponse:
        auth = get_container(request).auth
        token = auth.authenticate(payload.password)
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid administrator password")
        return LoginResponse(access_token=token)

    @app.get("/api/summary", response_model=SummaryResponse)
    async def summary(request: Request, _: None = Depends(require_admin)) -> SummaryResponse:
        data = get_container(request).repository.summary()
        return SummaryResponse(**data)

    @app.get("/api/accounts", response_model=list[AccountResponse])
    async def list_accounts(request: Request, _: None = Depends(require_admin)) -> list[AccountResponse]:
        accounts = get_container(request).repository.list_accounts()
        return [AccountResponse(**item) for item in accounts]

    @app.post("/api/accounts", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
    async def create_account(
        payload: AccountCreate, request: Request, _: None = Depends(require_admin)
    ) -> AccountResponse:
        account = get_container(request).repository.create_account(payload.model_dump())
        return AccountResponse(**account)

    @app.get("/api/accounts/{account_id}", response_model=AccountResponse)
    async def get_account(account_id: int, request: Request, _: None = Depends(require_admin)) -> AccountResponse:
        account = get_container(request).repository.get_account(account_id)
        return AccountResponse(**account)

    @app.patch("/api/accounts/{account_id}", response_model=AccountResponse)
    async def update_account(
        account_id: int, payload: AccountUpdate, request: Request, _: None = Depends(require_admin)
    ) -> AccountResponse:
        changes = payload.model_dump(exclude_unset=True)
        account = get_container(request).repository.update_account(account_id, changes)
        return AccountResponse(**account)

    @app.delete("/api/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_account(account_id: int, request: Request, _: None = Depends(require_admin)) -> Response:
        get_container(request).repository.delete_account(account_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/accounts/{account_id}/login", response_model=ActionResult)
    async def login_account(account_id: int, request: Request, _: None = Depends(require_admin)) -> ActionResult:
        # Playwright's synchronous API must run outside FastAPI's event loop.
        result = await run_in_threadpool(get_container(request).checkins.login, account_id)
        return ActionResult(**result)

    @app.post("/api/accounts/{account_id}/checkin", response_model=ActionResult)
    async def checkin_account(
        account_id: int,
        request: Request,
        _: None = Depends(require_admin),
        refresh_cookie: bool = Query(default=False),
    ) -> ActionResult:
        result = await run_in_threadpool(
            get_container(request).checkins.checkin,
            account_id,
            refresh_cookie=refresh_cookie,
        )
        return ActionResult(**result)

    @app.post("/api/checkins/run", response_model=BatchCheckinResponse)
    async def run_checkins(
        payload: CheckinRunRequest, request: Request, _: None = Depends(require_admin)
    ) -> BatchCheckinResponse:
        try:
            results = await run_in_threadpool(
                get_container(request).checkins.run_batch,
                payload.account_ids,
                enabled_only=payload.enabled_accounts_only,
                refresh_cookies=payload.refresh_cookies,
            )
        except OperationInProgressError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return BatchCheckinResponse(results=[ActionResult(**item) for item in results])

    @app.get("/api/proxies", response_model=list[ProxyResponse])
    async def list_proxies(request: Request, _: None = Depends(require_admin)) -> list[ProxyResponse]:
        proxies = get_container(request).repository.list_proxies()
        return [ProxyResponse(**item) for item in proxies]

    @app.post("/api/proxies", response_model=ProxyResponse, status_code=status.HTTP_201_CREATED)
    async def create_proxy(
        payload: ProxyCreate, request: Request, _: None = Depends(require_admin)
    ) -> ProxyResponse:
        proxy = get_container(request).repository.create_proxy(payload.model_dump())
        return ProxyResponse(**proxy)

    @app.patch("/api/proxies/assignments", response_model=ProxyAssignmentResponse)
    async def assign_proxy(
        payload: ProxyAssignmentRequest, request: Request, _: None = Depends(require_admin)
    ) -> ProxyAssignmentResponse:
        count = get_container(request).repository.assign_proxy(payload.account_ids, payload.proxy_id)
        return ProxyAssignmentResponse(updated_count=count)

    @app.patch("/api/proxies/{proxy_id}", response_model=ProxyResponse)
    async def update_proxy(
        proxy_id: int, payload: ProxyUpdate, request: Request, _: None = Depends(require_admin)
    ) -> ProxyResponse:
        changes = payload.model_dump(exclude_unset=True)
        proxy = get_container(request).repository.update_proxy(proxy_id, changes)
        return ProxyResponse(**proxy)

    @app.delete("/api/proxies/{proxy_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_proxy(proxy_id: int, request: Request, _: None = Depends(require_admin)) -> Response:
        get_container(request).repository.delete_proxy(proxy_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/config", response_model=SiteConfig)
    async def get_config(request: Request, _: None = Depends(require_admin)) -> SiteConfig:
        config = get_container(request).repository.get_site_config()
        return SiteConfig(**config)

    @app.put("/api/config", response_model=SiteConfig)
    async def update_config(payload: SiteConfig, request: Request, _: None = Depends(require_admin)) -> SiteConfig:
        container = get_container(request)
        saved = container.repository.save_site_config(payload.model_dump())
        container.scheduler.refresh()
        return SiteConfig(**saved)

    @app.get("/api/logs", response_model=list[LogResponse])
    async def logs(
        request: Request,
        _: None = Depends(require_admin),
        limit: int = Query(default=80, ge=1, le=500),
    ) -> list[LogResponse]:
        items = get_container(request).repository.list_logs(limit)
        return [LogResponse(**item) for item in items]

    @app.post("/api/import/legacy", response_model=LegacyImportResponse)
    async def import_legacy(
        payload: LegacyImportRequest, request: Request, _: None = Depends(require_admin)
    ) -> LegacyImportResponse:
        container = get_container(request)
        stats = import_legacy_files(
            container.repository,
            account_file=container.settings.legacy_account_file,
            proxy_file=container.settings.legacy_proxy_file,
            import_accounts=payload.import_accounts,
            import_proxies=payload.import_proxies,
        )
        return LegacyImportResponse(**stats.to_dict())

    static_dir = ROOT_DIR / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    else:
        @app.get("/")
        def root() -> FileResponse:
            raise HTTPException(status_code=404, detail="Frontend assets were not found")

    return app


app = create_app()
