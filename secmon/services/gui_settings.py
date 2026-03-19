from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import hmac
import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import SessionLocal, init_db
from ..models import AppConfigEntry
from .analysis import build_analysis_service, fetch_provider_model_catalog, normalize_provider_name
from .llm_usage import (
    DEFAULT_USAGE_PROVIDER_CONFIG,
    LlmUsageService,
    mask_secret,
    normalize_actor_type,
    normalize_metric_name,
    normalize_threshold_percentages,
    serialize_monitoring_config,
    usage_monitoring_defaults,
)


ANALYSIS_CONFIG_KEY = "analysis_runtime_config_v1"

PROVIDER_SPECS: dict[str, dict[str, Any]] = {
    "gemini": {
        "label": "Google Gemini",
        "secret_fields": ("api_key",),
        "fields": ("model", "api_key"),
    },
    "deepseek": {
        "label": "DeepSeek",
        "secret_fields": ("api_key",),
        "fields": ("model", "base_url", "api_key"),
    },
    "grok": {
        "label": "xAI Grok",
        "secret_fields": ("api_key",),
        "fields": ("model", "base_url", "api_key"),
    },
    "github": {
        "label": "GitHub Models",
        "secret_fields": ("token",),
        "fields": ("model", "base_url", "api_version", "org", "token"),
    },
    "copilot": {
        "label": "GitHub Copilot",
        "secret_fields": (),
        "fields": ("model", "node_binary"),
    },
}

NUMERIC_FIELDS = ("analysis_temperature", "analysis_max_tokens", "analysis_timeout_seconds")
TOP_LEVEL_FIELDS = ("analysis_provider", *NUMERIC_FIELDS)


class GuiAuthError(RuntimeError):
    pass


class GuiSettingsService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def auth_enabled(self) -> bool:
        return bool(self.settings.gui_admin_password)

    def bootstrap_payload(self) -> dict:
        init_db()
        effective_settings, runtime_source, stored_config, config_error = self._load_effective_settings()
        analysis_service = build_analysis_service(effective_settings)
        return {
            "auth_enabled": self.auth_enabled,
            "session_ttl_seconds": self.settings.gui_session_ttl_seconds,
            "runtime_source": runtime_source,
            "config_error": config_error,
            "current": {
                "provider": analysis_service.provider_name,
                "model": analysis_service.selected_model,
                "available": analysis_service.available,
                "reason": None if analysis_service.available else analysis_service.unavailable_reason,
            },
            "providers": [
                {
                    "id": provider_id,
                    "label": spec["label"],
                    "secret_required": bool(spec["secret_fields"]),
                }
                for provider_id, spec in PROVIDER_SPECS.items()
            ],
            "configured_in_gui": stored_config is not None,
            "usage_monitoring_defaults": usage_monitoring_defaults(),
        }

    def create_session_token(self, password: str) -> dict:
        self._require_auth_enabled()
        if not hmac.compare_digest(password, self.settings.gui_admin_password or ""):
            raise GuiAuthError("Invalid GUI admin password.")

        issued_at = int(time.time())
        expires_at = issued_at + self.settings.gui_session_ttl_seconds
        payload = {"iat": issued_at, "exp": expires_at, "scope": "gui-admin"}
        token = self._sign_token(payload)
        return {"token": token, "expires_at": expires_at}

    def verify_session_token(self, token: str | None) -> None:
        self._require_auth_enabled()
        if not token:
            raise GuiAuthError("Missing GUI admin token.")

        body, signature = self._split_token(token)
        expected = self._token_signature(body)
        if not hmac.compare_digest(signature, expected):
            raise GuiAuthError("Invalid GUI admin token signature.")

        payload = self._decode_token_body(body)
        if int(payload.get("exp", 0)) < int(time.time()):
            raise GuiAuthError("GUI admin token has expired.")

    def settings_payload(self) -> dict:
        self._require_auth_enabled()
        init_db()

        config_error: str | None = None
        with SessionLocal() as db:
            try:
                stored_config = self._load_stored_config(db)
            except Exception as exc:
                stored_config = None
                config_error = str(exc)

        effective_settings, runtime_source, _, effective_error = self._load_effective_settings(stored_config=stored_config)
        analysis_service = build_analysis_service(effective_settings)
        return {
            **self.bootstrap_payload(),
            "masked_config": self._build_masked_config(stored_config, effective_settings),
            "runtime_source": runtime_source,
            "config_error": config_error or effective_error,
            "current": {
                "provider": analysis_service.provider_name,
                "model": analysis_service.selected_model,
                "available": analysis_service.available,
                "reason": None if analysis_service.available else analysis_service.unavailable_reason,
            },
        }

    def save_settings(self, payload: dict) -> dict:
        self._require_auth_enabled()
        init_db()

        config_error: str | None = None
        with SessionLocal() as db:
            try:
                existing = self._load_stored_config(db) or {}
            except Exception as exc:
                existing = {}
                config_error = str(exc)
            normalized = self._normalize_payload(payload, existing)
            self._save_stored_config(db, normalized)
            db.commit()

        effective_settings, runtime_source, stored_config, effective_error = self._load_effective_settings()
        analysis_service = build_analysis_service(effective_settings)
        return {
            "saved": True,
            "runtime_source": runtime_source,
            "config_error": config_error or effective_error,
            "current": {
                "provider": analysis_service.provider_name,
                "model": analysis_service.selected_model,
                "available": analysis_service.available,
                "reason": None if analysis_service.available else analysis_service.unavailable_reason,
            },
            "masked_config": self._build_masked_config(stored_config, effective_settings),
        }

    def test_settings(self, payload: dict | None = None) -> dict:
        self._require_auth_enabled()
        init_db()

        with SessionLocal() as db:
            try:
                existing = self._load_stored_config(db) or {}
            except Exception:
                existing = {}

        normalized = self._normalize_payload(payload or existing, existing)
        effective_settings = self.apply_runtime_settings(self.settings, normalized)
        analysis_service = build_analysis_service(effective_settings)
        if not analysis_service.available:
            return {
                "ok": False,
                "provider": analysis_service.provider_name,
                "model": analysis_service.selected_model,
                "reason": analysis_service.unavailable_reason,
            }

        try:
            completion = analysis_service.complete_text(
                system_prompt="Reply with exactly one short line: OK",
                user_prompt="OK",
            )
            with SessionLocal() as db:
                LlmUsageService(effective_settings, lambda: normalized).record_usage(
                    db,
                    provider=completion.provider,
                    model=completion.model,
                    feature="gui_test",
                    usage=completion.usage,
                    raw_payload=completion.raw_payload,
                )
                db.commit()
            return {
                "ok": True,
                "provider": completion.provider,
                "model": completion.model,
                "response_preview": completion.text.strip()[:200],
            }
        except Exception as exc:
            return {
                "ok": False,
                "provider": analysis_service.provider_name,
                "model": analysis_service.selected_model,
                "reason": str(exc),
            }

    def list_provider_models(self, provider_id: str, payload: dict | None = None) -> dict:
        self._require_auth_enabled()
        init_db()

        provider = normalize_provider_name(provider_id)
        if provider not in PROVIDER_SPECS:
            raise RuntimeError(f"Unsupported provider '{provider_id}'.")

        with SessionLocal() as db:
            try:
                existing = self._load_stored_config(db) or {}
            except Exception:
                existing = {}

        normalized = self._normalize_payload(payload or existing, existing)
        effective_settings = self.apply_runtime_settings(self.settings, normalized)
        return fetch_provider_model_catalog(effective_settings, provider)

    def apply_runtime_settings(self, base_settings: Settings, runtime_config: dict | None = None) -> Settings:
        config = runtime_config or self.load_runtime_config()
        if not config:
            return base_settings

        providers = config.get("providers", {}) if isinstance(config.get("providers"), dict) else {}
        gemini = providers.get("gemini", {})
        deepseek = providers.get("deepseek", {})
        grok = providers.get("grok", {})
        github = providers.get("github", {})
        copilot = providers.get("copilot", {})

        return replace(
            base_settings,
            analysis_provider=normalize_provider_name(str(config.get("analysis_provider", base_settings.analysis_provider))),
            analysis_temperature=float(config.get("analysis_temperature", base_settings.analysis_temperature)),
            analysis_max_tokens=int(config.get("analysis_max_tokens", base_settings.analysis_max_tokens)),
            analysis_timeout_seconds=int(config.get("analysis_timeout_seconds", base_settings.analysis_timeout_seconds)),
            analysis_model=None,
            gemini_api_key=self._pick_override(gemini, "api_key", base_settings.gemini_api_key),
            gemini_model=self._pick_override(gemini, "model", base_settings.gemini_model),
            deepseek_api_key=self._pick_override(deepseek, "api_key", base_settings.deepseek_api_key),
            deepseek_model=self._pick_override(deepseek, "model", base_settings.deepseek_model),
            deepseek_base_url=self._pick_override(deepseek, "base_url", base_settings.deepseek_base_url),
            xai_api_key=self._pick_override(grok, "api_key", base_settings.xai_api_key),
            xai_model=self._pick_override(grok, "model", base_settings.xai_model),
            xai_base_url=self._pick_override(grok, "base_url", base_settings.xai_base_url),
            github_models_token=self._pick_override(github, "token", base_settings.github_models_token),
            github_models_model=self._pick_override(github, "model", base_settings.github_models_model),
            github_models_base_url=self._pick_override(github, "base_url", base_settings.github_models_base_url),
            github_models_api_version=self._pick_override(github, "api_version", base_settings.github_models_api_version),
            github_models_org=self._pick_override(github, "org", base_settings.github_models_org),
            copilot_model=self._pick_override(copilot, "model", base_settings.copilot_model),
            copilot_node_binary=self._pick_override(copilot, "node_binary", base_settings.copilot_node_binary),
        )

    def load_runtime_config(self) -> dict | None:
        init_db()
        if not self.auth_enabled:
            return None
        with SessionLocal() as db:
            try:
                return self._load_stored_config(db)
            except Exception:
                return None

    def _load_effective_settings(
        self,
        *,
        stored_config: dict | None = None,
    ) -> tuple[Settings, str, dict | None, str | None]:
        config_error: str | None = None
        if stored_config is not None:
            runtime_config = stored_config
        else:
            try:
                runtime_config = self.load_runtime_config()
            except Exception as exc:
                runtime_config = None
                config_error = str(exc)
        effective_settings = self.apply_runtime_settings(self.settings, runtime_config)
        runtime_source = "gui" if runtime_config else "env"
        return effective_settings, runtime_source, runtime_config, config_error

    def _build_masked_config(self, stored_config: dict | None, effective_settings: Settings) -> dict:
        config = stored_config or {}
        providers_config = config.get("providers", {}) if isinstance(config.get("providers"), dict) else {}
        return {
            "analysis_provider": config.get("analysis_provider", effective_settings.analysis_provider),
            "analysis_temperature": config.get("analysis_temperature", effective_settings.analysis_temperature),
            "analysis_max_tokens": config.get("analysis_max_tokens", effective_settings.analysis_max_tokens),
            "analysis_timeout_seconds": config.get("analysis_timeout_seconds", effective_settings.analysis_timeout_seconds),
            "providers": {
                "gemini": {
                    "model": self._masked_value(providers_config.get("gemini", {}), "model", effective_settings.gemini_model),
                    "has_api_key": self._has_secret(providers_config.get("gemini", {}), "api_key", effective_settings.gemini_api_key),
                    "api_key_masked": self._masked_secret(providers_config.get("gemini", {}), "api_key", effective_settings.gemini_api_key),
                },
                "deepseek": {
                    "model": self._masked_value(providers_config.get("deepseek", {}), "model", effective_settings.deepseek_model),
                    "base_url": self._masked_value(providers_config.get("deepseek", {}), "base_url", effective_settings.deepseek_base_url),
                    "has_api_key": self._has_secret(providers_config.get("deepseek", {}), "api_key", effective_settings.deepseek_api_key),
                    "api_key_masked": self._masked_secret(providers_config.get("deepseek", {}), "api_key", effective_settings.deepseek_api_key),
                },
                "grok": {
                    "model": self._masked_value(providers_config.get("grok", {}), "model", effective_settings.xai_model),
                    "base_url": self._masked_value(providers_config.get("grok", {}), "base_url", effective_settings.xai_base_url),
                    "has_api_key": self._has_secret(providers_config.get("grok", {}), "api_key", effective_settings.xai_api_key),
                    "api_key_masked": self._masked_secret(providers_config.get("grok", {}), "api_key", effective_settings.xai_api_key),
                },
                "github": {
                    "model": self._masked_value(providers_config.get("github", {}), "model", effective_settings.github_models_model),
                    "base_url": self._masked_value(providers_config.get("github", {}), "base_url", effective_settings.github_models_base_url),
                    "api_version": self._masked_value(
                        providers_config.get("github", {}),
                        "api_version",
                        effective_settings.github_models_api_version,
                    ),
                    "org": self._masked_value(providers_config.get("github", {}), "org", effective_settings.github_models_org or ""),
                    "has_token": self._has_secret(providers_config.get("github", {}), "token", effective_settings.github_models_token),
                    "token_masked": self._masked_secret(providers_config.get("github", {}), "token", effective_settings.github_models_token),
                },
                "copilot": {
                    "model": self._masked_value(providers_config.get("copilot", {}), "model", effective_settings.copilot_model),
                    "node_binary": self._masked_value(
                        providers_config.get("copilot", {}),
                        "node_binary",
                        effective_settings.copilot_node_binary,
                    ),
                },
            },
            "usage_monitoring": self._build_masked_usage_monitoring_config(config),
        }

    def _load_stored_config(self, db: Session) -> dict | None:
        entry = db.execute(select(AppConfigEntry).where(AppConfigEntry.key == ANALYSIS_CONFIG_KEY)).scalar_one_or_none()
        if entry is None:
            return None
        decrypted = self._decrypt(entry.value)
        return json.loads(decrypted)

    def _save_stored_config(self, db: Session, config: dict) -> None:
        serialized = json.dumps(config, ensure_ascii=False)
        encrypted = self._encrypt(serialized)
        entry = db.execute(select(AppConfigEntry).where(AppConfigEntry.key == ANALYSIS_CONFIG_KEY)).scalar_one_or_none()
        if entry is None:
            db.add(AppConfigEntry(key=ANALYSIS_CONFIG_KEY, value=encrypted, is_secret=True))
            return
        entry.value = encrypted
        entry.is_secret = True

    def _normalize_payload(self, payload: dict, existing: dict | None) -> dict:
        current = existing or {}
        normalized: dict[str, Any] = {
            "analysis_provider": normalize_provider_name(str(payload.get("analysis_provider", current.get("analysis_provider", self.settings.analysis_provider)))),
            "analysis_temperature": self._coerce_float(
                payload.get("analysis_temperature", current.get("analysis_temperature", self.settings.analysis_temperature)),
                self.settings.analysis_temperature,
            ),
            "analysis_max_tokens": self._coerce_int(
                payload.get("analysis_max_tokens", current.get("analysis_max_tokens", self.settings.analysis_max_tokens)),
                self.settings.analysis_max_tokens,
            ),
            "analysis_timeout_seconds": self._coerce_int(
                payload.get("analysis_timeout_seconds", current.get("analysis_timeout_seconds", self.settings.analysis_timeout_seconds)),
                self.settings.analysis_timeout_seconds,
            ),
            "providers": {},
            "usage_monitoring": {"providers": {}},
        }

        incoming_providers = payload.get("providers", {}) if isinstance(payload.get("providers"), dict) else {}
        current_providers = current.get("providers", {}) if isinstance(current.get("providers"), dict) else {}
        clear_secret_fields = payload.get("clear_secret_fields", {}) if isinstance(payload.get("clear_secret_fields"), dict) else {}

        for provider_id, spec in PROVIDER_SPECS.items():
            provider_payload = incoming_providers.get(provider_id, {})
            provider_current = current_providers.get(provider_id, {})
            if not isinstance(provider_payload, dict):
                provider_payload = {}
            if not isinstance(provider_current, dict):
                provider_current = {}

            provider_out: dict[str, Any] = {}
            provider_clear = clear_secret_fields.get(provider_id, [])
            if not isinstance(provider_clear, list):
                provider_clear = []

            for field in spec["fields"]:
                incoming_value = provider_payload.get(field)
                current_value = provider_current.get(field)

                if field in spec["secret_fields"]:
                    if field in provider_clear:
                        provider_out[field] = None
                    elif isinstance(incoming_value, str) and incoming_value.strip():
                        provider_out[field] = incoming_value.strip()
                    elif incoming_value is None or str(incoming_value).strip() == "":
                        provider_out[field] = current_value
                    else:
                        provider_out[field] = incoming_value
                    continue

                if incoming_value is None:
                    incoming_value = current_value

                if field in {"model", "base_url", "api_version", "org", "node_binary"}:
                    if incoming_value is None:
                        continue
                    provider_out[field] = str(incoming_value).strip()
                    continue

                provider_out[field] = incoming_value

            normalized["providers"][provider_id] = provider_out

        current_usage = current.get("usage_monitoring", {}) if isinstance(current.get("usage_monitoring"), dict) else {}
        incoming_usage = payload.get("usage_monitoring", {}) if isinstance(payload.get("usage_monitoring"), dict) else {}
        current_usage_providers = current_usage.get("providers", {}) if isinstance(current_usage.get("providers"), dict) else {}
        incoming_usage_providers = incoming_usage.get("providers", {}) if isinstance(incoming_usage.get("providers"), dict) else {}

        for provider_id, defaults in DEFAULT_USAGE_PROVIDER_CONFIG.items():
            provider_current = current_usage_providers.get(provider_id, {})
            provider_incoming = incoming_usage_providers.get(provider_id, {})
            if not isinstance(provider_current, dict):
                provider_current = {}
            if not isinstance(provider_incoming, dict):
                provider_incoming = {}

            provider_usage: dict[str, Any] = {
                "enabled": self._coerce_bool(provider_incoming.get("enabled", provider_current.get("enabled", defaults.get("enabled", False)))),
                "alert_enabled": self._coerce_bool(
                    provider_incoming.get("alert_enabled", provider_current.get("alert_enabled", defaults.get("alert_enabled", True)))
                ),
                "prefer_provider_api": self._coerce_bool(
                    provider_incoming.get("prefer_provider_api", provider_current.get("prefer_provider_api", defaults.get("prefer_provider_api", False)))
                ),
                "metric_name": normalize_metric_name(
                    provider_incoming.get("metric_name", provider_current.get("metric_name", defaults.get("metric_name"))),
                    provider_id,
                ),
                "limit_value": self._coerce_float(
                    provider_incoming.get("limit_value", provider_current.get("limit_value", defaults.get("limit_value", 0.0))),
                    float(defaults.get("limit_value", 0.0)),
                ),
                "threshold_percentages": normalize_threshold_percentages(
                    provider_incoming.get(
                        "threshold_percentages",
                        provider_current.get("threshold_percentages", defaults.get("threshold_percentages", [])),
                    )
                ),
                "billing_actor_type": normalize_actor_type(
                    provider_incoming.get(
                        "billing_actor_type",
                        provider_current.get("billing_actor_type", defaults.get("billing_actor_type", "user")),
                    )
                ),
                "billing_actor": str(
                    provider_incoming.get(
                        "billing_actor",
                        provider_current.get("billing_actor", defaults.get("billing_actor", "")),
                    )
                    or ""
                ).strip(),
            }

            incoming_billing_token = provider_incoming.get("billing_token")
            current_billing_token = provider_current.get("billing_token")
            if isinstance(incoming_billing_token, str) and incoming_billing_token.strip():
                provider_usage["billing_token"] = incoming_billing_token.strip()
            elif incoming_billing_token is None or str(incoming_billing_token).strip() == "":
                provider_usage["billing_token"] = current_billing_token
            else:
                provider_usage["billing_token"] = incoming_billing_token

            normalized["usage_monitoring"]["providers"][provider_id] = provider_usage

        return normalized

    def _require_auth_enabled(self) -> None:
        if self.auth_enabled:
            return
        raise GuiAuthError("GUI admin password is not configured. Set GUI_ADMIN_PASSWORD first.")

    def _encrypt(self, value: str) -> str:
        fernet = self._get_fernet()
        return fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def _decrypt(self, value: str) -> str:
        fernet = self._get_fernet()
        return fernet.decrypt(value.encode("utf-8")).decode("utf-8")

    def _get_fernet(self):  # type: ignore[no-untyped-def]
        self._require_auth_enabled()
        try:
            from cryptography.fernet import Fernet
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("cryptography is required for GUI settings encryption. Install requirements.txt.") from exc

        salt = hashlib.sha256(self.settings.gui_config_salt.encode("utf-8")).digest()[:16]
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480000)
        key = base64.urlsafe_b64encode(kdf.derive((self.settings.gui_admin_password or "").encode("utf-8")))
        return Fernet(key)

    def _sign_token(self, payload: dict[str, Any]) -> str:
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8").rstrip("=")
        return f"{body}.{self._token_signature(body)}"

    def _split_token(self, token: str) -> tuple[str, str]:
        body, dot, signature = token.partition(".")
        if not dot:
            raise GuiAuthError("Malformed GUI admin token.")
        return body, signature

    def _decode_token_body(self, body: str) -> dict[str, Any]:
        padded = body + "=" * (-len(body) % 4)
        try:
            return json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
        except Exception as exc:
            raise GuiAuthError("Malformed GUI admin token payload.") from exc

    def _token_signature(self, body: str) -> str:
        secret = hashlib.sha256(
            f"{self.settings.gui_admin_password or ''}:{self.settings.gui_config_salt}:{self.settings.app_name}".encode("utf-8")
        ).digest()
        return hmac.new(secret, body.encode("utf-8"), hashlib.sha256).hexdigest()

    def _pick_override(self, provider_payload: dict, key: str, fallback: Any) -> Any:
        if key not in provider_payload:
            return fallback
        value = provider_payload.get(key)
        if value is None:
            return fallback
        if isinstance(value, str) and not value.strip():
            return fallback
        return value

    def _masked_value(self, provider_payload: dict, key: str, fallback: Any) -> Any:
        if key in provider_payload:
            value = provider_payload.get(key)
            if value is None:
                return fallback
            if isinstance(value, str) and not value.strip():
                return fallback
            return value
        return fallback

    def _has_secret(self, provider_payload: dict, key: str, fallback: Any) -> bool:
        if key in provider_payload:
            return bool(provider_payload.get(key))
        return bool(fallback)

    def _masked_secret(self, provider_payload: dict, key: str, fallback: Any) -> str | None:
        if key in provider_payload:
            return self._mask_secret(provider_payload.get(key))
        return self._mask_secret(fallback)

    def _mask_secret(self, value: Any) -> str | None:
        return mask_secret(value)

    def _coerce_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _coerce_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _coerce_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _build_masked_usage_monitoring_config(self, config: dict | None) -> dict:
        raw_usage = config.get("usage_monitoring", {}) if isinstance(config, dict) else {}
        raw_providers = raw_usage.get("providers", {}) if isinstance(raw_usage, dict) else {}
        output = {"providers": {}}
        for provider_id, defaults in DEFAULT_USAGE_PROVIDER_CONFIG.items():
            provider = raw_providers.get(provider_id, {}) if isinstance(raw_providers, dict) else {}
            if not isinstance(provider, dict):
                provider = {}
            merged = {**defaults, **provider}
            output["providers"][provider_id] = serialize_monitoring_config(merged)
        return output
