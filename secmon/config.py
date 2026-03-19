from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PUBLIC_DIR = BASE_DIR / "public"

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    return float(value)


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _env_optional_str(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _env_csv_int(name: str, default: str) -> tuple[int, ...]:
    return tuple(int(item) for item in _env_csv(name, default))


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    log_level: str
    database_url: str
    sec_feed_url: str
    sec_user_agent: str
    request_timeout_seconds: int
    poll_interval_seconds: int
    max_feed_entries: int
    max_document_chars: int
    fetch_full_text_for_all: bool
    analyze_tiers: tuple[int, ...]
    telegram_tiers: tuple[int, ...]
    analysis_provider: str
    analysis_model: str | None
    analysis_temperature: float
    analysis_max_tokens: int
    analysis_timeout_seconds: int
    gemini_api_key: str | None
    gemini_model: str
    deepseek_api_key: str | None
    deepseek_model: str
    deepseek_base_url: str
    xai_api_key: str | None
    xai_model: str
    xai_base_url: str
    copilot_model: str
    copilot_node_binary: str
    github_models_token: str | None
    github_models_model: str
    github_models_base_url: str
    github_models_api_version: str
    github_models_org: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    telegram_assistant_enabled: bool
    telegram_allowed_chat_ids: tuple[str, ...]
    telegram_updates_limit: int
    telegram_updates_timeout_seconds: int
    telegram_updates_poll_interval_seconds: int
    telegram_assistant_context_filings: int
    telegram_assistant_history_messages: int
    telegram_webhook_secret: str | None
    gui_admin_password: str | None
    gui_session_ttl_seconds: int
    gui_config_salt: str
    llm_chunk_size: int
    llm_chunk_overlap: int
    allowed_origins: tuple[str, ...]
    dashboard_recent_limit: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    default_db = f"sqlite:///{(DATA_DIR / 'sec_monitor.db').resolve().as_posix()}"
    github_models_token = _env_optional_str("GITHUB_MODELS_TOKEN") or _env_optional_str("GITHUB_TOKEN")
    xai_api_key = _env_optional_str("XAI_API_KEY") or _env_optional_str("GROK_API_KEY")
    return Settings(
        app_name=_env_str("APP_NAME", "SEC Monitor"),
        app_env=_env_str("APP_ENV", "development"),
        log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        database_url=_env_str("DATABASE_URL", default_db),
        sec_feed_url=os.getenv(
            "SEC_FEED_URL",
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&CIK=&type=&company=&dateb=&owner=include&start=0&count=100&output=atom",
        ),
        sec_user_agent=_env_str("SEC_USER_AGENT", "SEC Monitor/2.0 contact@example.com"),
        request_timeout_seconds=_env_int("REQUEST_TIMEOUT_SECONDS", 20),
        poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 180),
        max_feed_entries=_env_int("MAX_FEED_ENTRIES", 100),
        max_document_chars=_env_int("MAX_DOCUMENT_CHARS", 50000),
        fetch_full_text_for_all=_env_bool("FETCH_FULL_TEXT_FOR_ALL", True),
        analyze_tiers=_env_csv_int("ANALYZE_TIERS", "1,2"),
        telegram_tiers=_env_csv_int("TELEGRAM_TIERS", "1,2"),
        analysis_provider=_env_str("ANALYSIS_PROVIDER", "gemini").lower(),
        analysis_model=_env_optional_str("ANALYSIS_MODEL"),
        analysis_temperature=_env_float("ANALYSIS_TEMPERATURE", 0.1),
        analysis_max_tokens=_env_int("ANALYSIS_MAX_TOKENS", 900),
        analysis_timeout_seconds=_env_int("ANALYSIS_TIMEOUT_SECONDS", 120),
        gemini_api_key=_env_optional_str("GEMINI_API_KEY"),
        gemini_model=_env_str("GEMINI_MODEL", "gemini-2.5-flash"),
        deepseek_api_key=_env_optional_str("DEEPSEEK_API_KEY"),
        deepseek_model=_env_str("DEEPSEEK_MODEL", "deepseek-chat"),
        deepseek_base_url=_env_str("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        xai_api_key=xai_api_key,
        xai_model=_env_str("XAI_MODEL", _env_str("GROK_MODEL", "grok-4")),
        xai_base_url=_env_str("XAI_BASE_URL", "https://api.x.ai/v1"),
        copilot_model=_env_str("COPILOT_MODEL", "gpt-4.1"),
        copilot_node_binary=_env_str("COPILOT_NODE_BINARY", "node"),
        github_models_token=github_models_token,
        github_models_model=_env_str("GITHUB_MODELS_MODEL", "openai/gpt-4.1"),
        github_models_base_url=_env_str("GITHUB_MODELS_BASE_URL", "https://models.github.ai"),
        github_models_api_version=_env_str("GITHUB_MODELS_API_VERSION", "2026-03-10"),
        github_models_org=_env_optional_str("GITHUB_MODELS_ORG"),
        telegram_bot_token=_env_optional_str("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env_optional_str("TELEGRAM_CHAT_ID"),
        telegram_assistant_enabled=_env_bool("TELEGRAM_ASSISTANT_ENABLED", True),
        telegram_allowed_chat_ids=_env_csv("TELEGRAM_ALLOWED_CHAT_IDS", ""),
        telegram_updates_limit=_env_int("TELEGRAM_UPDATES_LIMIT", 50),
        telegram_updates_timeout_seconds=_env_int("TELEGRAM_UPDATES_TIMEOUT_SECONDS", 25),
        telegram_updates_poll_interval_seconds=_env_int("TELEGRAM_UPDATES_POLL_INTERVAL_SECONDS", 3),
        telegram_assistant_context_filings=_env_int("TELEGRAM_ASSISTANT_CONTEXT_FILINGS", 6),
        telegram_assistant_history_messages=_env_int("TELEGRAM_ASSISTANT_HISTORY_MESSAGES", 6),
        telegram_webhook_secret=_env_optional_str("TELEGRAM_WEBHOOK_SECRET"),
        gui_admin_password=_env_optional_str("GUI_ADMIN_PASSWORD"),
        gui_session_ttl_seconds=_env_int("GUI_SESSION_TTL_SECONDS", 28800),
        gui_config_salt=_env_str("GUI_CONFIG_SALT", "sec-monitor-gui-config"),
        llm_chunk_size=_env_int("LLM_CHUNK_SIZE", 1800),
        llm_chunk_overlap=_env_int("LLM_CHUNK_OVERLAP", 250),
        allowed_origins=_env_csv("ALLOWED_ORIGINS", "*"),
        dashboard_recent_limit=_env_int("DASHBOARD_RECENT_LIMIT", 60),
    )
