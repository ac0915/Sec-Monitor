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


@app.post("/api/integrations/telegram/webhook")
def telegram_webhook(
    payload: dict,
    secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict:
    if settings.telegram_webhook_secret and secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")
    return IngestionService().process_telegram_update(payload)
