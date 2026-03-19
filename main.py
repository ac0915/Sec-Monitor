from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from secmon.config import PUBLIC_DIR, get_settings
from secmon.database import init_db
from secmon.logging_utils import configure_logging
from secmon.pipeline import IngestionService
from secmon.services.auth import AuthError, AuthService
from secmon.services.gui_settings import GuiAuthError, GuiSettingsService
from secmon.services.llm_usage import LlmUsageService


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(settings.log_level)
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=settings.allowed_origins != ("*",),
    allow_methods=["*"],
    allow_headers=["*"],
)


def build_gui_settings_service() -> GuiSettingsService:
    return GuiSettingsService(settings)


def build_auth_service() -> AuthService:
    return AuthService(settings)


def build_llm_usage_service() -> LlmUsageService:
    gui_service = build_gui_settings_service()
    return LlmUsageService(settings, gui_service.load_runtime_config)


def extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def raise_gui_http_error(exc: Exception, *, default_status: int = 400) -> None:
    detail = str(exc)
    if isinstance(exc, AuthError) and "missing" in detail.lower():
        raise HTTPException(status_code=401, detail=detail)
    if isinstance(exc, AuthError):
        raise HTTPException(status_code=default_status, detail=detail)
    if isinstance(exc, GuiAuthError) and "not configured" in detail.lower():
        raise HTTPException(status_code=503, detail=detail)
    if isinstance(exc, GuiAuthError):
        raise HTTPException(status_code=default_status, detail=detail)
    if isinstance(exc, (RuntimeError, ValueError)):
        raise HTTPException(status_code=default_status, detail=detail)
    raise HTTPException(status_code=500, detail=detail)


def authorize_gui_request(authorization: str | None) -> GuiSettingsService:
    token = extract_bearer_token(authorization)
    try:
        build_auth_service().verify_access_token(token, required_role="admin")
    except Exception as exc:
        raise_gui_http_error(exc, default_status=401)
    return build_gui_settings_service()


def authorize_admin_request(authorization: str | None, *, required_role: str):
    token = extract_bearer_token(authorization)
    try:
        return build_auth_service().verify_access_token(token, required_role=required_role)
    except Exception as exc:
        raise_gui_http_error(exc, default_status=401)


@app.get("/", include_in_schema=False)
def serve_index() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/404", include_in_schema=False)
def serve_404() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "404.html")


@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
def health() -> dict:
    snapshot = IngestionService().dashboard_snapshot()
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.app_env,
        "database_backend": settings.database_url.split(":", 1)[0],
        "analysis": snapshot["analysis"],
        "telegram": snapshot["telegram"],
        "data_policy": snapshot["data_policy"],
        "last_run": snapshot["last_run"],
    }


@app.get("/api/dashboard")
def dashboard() -> dict:
    return IngestionService().dashboard_snapshot()


@app.get("/api/feed")
def filing_feed(
    q: str = "",
    tier: str = "all",
    ticker: str = "",
    form_type: str = "",
    impact: str = "all",
    theme: str = "all",
    tickers: str = "",
    limit: int = Query(default=80, ge=1, le=250),
) -> dict:
    return IngestionService().feed_snapshot(
        query=q,
        tier=tier,
        ticker=ticker,
        form_type=form_type,
        impact=impact,
        theme=theme,
        tickers=tuple(item.strip().upper() for item in tickers.split(",") if item.strip()),
        limit=limit,
    )


@app.get("/api/filings/{filing_id}")
def filing_detail(filing_id: int) -> dict:
    payload = IngestionService().filing_detail(filing_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Filing not found")
    return payload


@app.get("/api/gui/llm/bootstrap")
def gui_llm_bootstrap() -> dict:
    service = build_gui_settings_service()
    try:
        return service.bootstrap_payload()
    except Exception as exc:
        raise_gui_http_error(exc)


@app.post("/api/gui/auth/login")
def gui_auth_login(payload: dict) -> dict:
    service = build_auth_service()
    try:
        return service.login_with_bootstrap_password(str(payload.get("password", "")), label="gui")
    except Exception as exc:
        raise_gui_http_error(exc, default_status=401)


@app.get("/api/gui/llm/settings")
def gui_llm_settings(authorization: str | None = Header(default=None, alias="Authorization")) -> dict:
    service = authorize_gui_request(authorization)
    try:
        return service.settings_payload()
    except Exception as exc:
        raise_gui_http_error(exc)


@app.put("/api/gui/llm/settings")
def gui_llm_settings_save(
    payload: dict,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    service = authorize_gui_request(authorization)
    try:
        return service.save_settings(payload)
    except Exception as exc:
        raise_gui_http_error(exc)


@app.post("/api/gui/llm/settings/test")
def gui_llm_settings_test(
    payload: dict | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    service = authorize_gui_request(authorization)
    try:
        return service.test_settings(payload)
    except Exception as exc:
        raise_gui_http_error(exc)


@app.post("/api/gui/llm/providers/{provider_id}/models")
def gui_llm_provider_models(
    provider_id: str,
    payload: dict | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    service = authorize_gui_request(authorization)
    try:
        return service.list_provider_models(provider_id, payload)
    except Exception as exc:
        raise_gui_http_error(exc)


@app.get("/api/gui/llm/usage")
def gui_llm_usage_summary(authorization: str | None = Header(default=None, alias="Authorization")) -> dict:
    authorize_gui_request(authorization)
    try:
        return build_llm_usage_service().summary_payload()
    except Exception as exc:
        raise_gui_http_error(exc)


@app.post("/api/gui/llm/usage/sync")
def gui_llm_usage_sync(
    payload: dict | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    authorize_gui_request(authorization)
    try:
        provider = str((payload or {}).get("provider", "")).strip() or None
        return build_llm_usage_service().sync_provider_usage_once(provider)
    except Exception as exc:
        raise_gui_http_error(exc)


@app.post("/api/admin/auth/login")
def admin_auth_login(payload: dict) -> dict:
    service = build_auth_service()
    try:
        username = str(payload.get("username", settings.admin_bootstrap_username))
        password = str(payload.get("password", ""))
        label = str(payload.get("label", "api"))
        return service.login(username=username, password=password, label=label)
    except Exception as exc:
        raise_gui_http_error(exc, default_status=401)


@app.get("/api/admin/me")
def admin_me(authorization: str | None = Header(default=None, alias="Authorization")) -> dict:
    token = extract_bearer_token(authorization)
    try:
        return build_auth_service().me(token)
    except Exception as exc:
        raise_gui_http_error(exc, default_status=401)


@app.get("/api/admin/users")
def admin_users(authorization: str | None = Header(default=None, alias="Authorization")) -> dict:
    principal = authorize_admin_request(authorization, required_role="admin")
    try:
        return build_auth_service().list_users(principal)
    except Exception as exc:
        raise_gui_http_error(exc)


@app.post("/api/admin/users")
def admin_users_create(
    payload: dict,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    principal = authorize_admin_request(authorization, required_role="admin")
    try:
        return build_auth_service().create_user(
            principal,
            username=str(payload.get("username", "")),
            password=str(payload.get("password", "")),
            role=str(payload.get("role", "operator")),
        )
    except Exception as exc:
        raise_gui_http_error(exc)


@app.get("/api/admin/audit")
def admin_audit(
    authorization: str | None = Header(default=None, alias="Authorization"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    principal = authorize_admin_request(authorization, required_role="admin")
    try:
        return build_auth_service().list_audit_events(principal, limit=limit)
    except Exception as exc:
        raise_gui_http_error(exc)


@app.get("/api/prices/latest")
def prices_latest(
    tickers: str = "",
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    return IngestionService().latest_prices_snapshot(
        tickers=tuple(item.strip().upper() for item in tickers.split(",") if item.strip()),
        limit=limit,
    )


@app.post("/api/admin/prices/sync")
def admin_prices_sync(
    payload: dict | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    principal = authorize_admin_request(authorization, required_role="operator")
    try:
        result = IngestionService().sync_prices_once(
            tickers=tuple(str(item).upper() for item in (payload or {}).get("tickers", []) if str(item).strip())
        )
        build_auth_service().record_admin_action(principal, action="prices.sync", resource_type="price_snapshot", metadata=result)
        return result
    except Exception as exc:
        raise_gui_http_error(exc)


@app.post("/api/admin/embeddings/run")
def admin_embeddings_run(
    payload: dict | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    principal = authorize_admin_request(authorization, required_role="operator")
    try:
        result = IngestionService().embed_chunks_once(limit=int((payload or {}).get("limit", settings.embedding_batch_limit)))
        build_auth_service().record_admin_action(principal, action="embeddings.run", resource_type="filing_chunk_embedding", metadata=result)
        return result
    except Exception as exc:
        raise_gui_http_error(exc)


@app.get("/api/admin/labels/tasks")
def admin_labels_tasks(
    authorization: str | None = Header(default=None, alias="Authorization"),
    status: str = "open",
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    authorize_admin_request(authorization, required_role="operator")
    return IngestionService().list_label_tasks(status=status, limit=limit)


@app.post("/api/admin/labels/seed")
def admin_labels_seed(
    payload: dict | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    principal = authorize_admin_request(authorization, required_role="operator")
    try:
        result = IngestionService().seed_label_tasks(
            limit=int((payload or {}).get("limit", settings.eval_default_limit)),
            principal=principal,
        )
        build_auth_service().record_admin_action(principal, action="labels.seed", resource_type="label_task", metadata=result)
        return result
    except Exception as exc:
        raise_gui_http_error(exc)


@app.post("/api/admin/labels/tasks/{task_id}/submit")
def admin_label_submit(
    task_id: int,
    payload: dict,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    principal = authorize_admin_request(authorization, required_role="operator")
    try:
        result = IngestionService().submit_label(task_id=task_id, payload=payload, principal=principal)
        build_auth_service().record_admin_action(
            principal,
            action="labels.submit",
            resource_type="label_task",
            resource_id=str(task_id),
            metadata={"label_id": result["label_id"]},
        )
        return result
    except Exception as exc:
        raise_gui_http_error(exc)


@app.get("/api/admin/evals")
def admin_evals(
    authorization: str | None = Header(default=None, alias="Authorization"),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    authorize_admin_request(authorization, required_role="operator")
    return IngestionService().list_evaluation_runs(limit=limit)


@app.get("/api/admin/evals/latest")
def admin_evals_latest(authorization: str | None = Header(default=None, alias="Authorization")) -> dict | None:
    authorize_admin_request(authorization, required_role="operator")
    return IngestionService().latest_evaluation_run()


@app.post("/api/admin/evals/run")
def admin_evals_run(
    payload: dict | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    principal = authorize_admin_request(authorization, required_role="operator")
    try:
        result = IngestionService().run_evaluation(
            limit=int((payload or {}).get("limit", settings.eval_default_limit)),
            principal=principal,
        )
        build_auth_service().record_admin_action(principal, action="evals.run", resource_type="evaluation_run", metadata=result)
        return result
    except Exception as exc:
        raise_gui_http_error(exc)


@app.post("/api/integrations/telegram/webhook")
def telegram_webhook(
    payload: dict,
    secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict:
    if settings.telegram_webhook_secret and secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")
    return IngestionService().process_telegram_update(payload)
