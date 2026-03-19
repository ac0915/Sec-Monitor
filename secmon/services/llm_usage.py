from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Callable

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import SessionLocal, init_db
from ..models import LlmUsageAlertEvent, LlmUsageEvent, LlmUsageSnapshot, utc_now
from .analysis import UsageMetrics, normalize_provider_name


logger = logging.getLogger(__name__)

USAGE_SUPPORTED_PROVIDERS = ("gemini", "deepseek", "grok", "github", "copilot")
DEFAULT_THRESHOLD_PERCENTAGES = (50.0, 80.0, 90.0, 100.0)
METRIC_UNIT_BY_NAME = {
    "total_tokens": "tokens",
    "prompt_tokens": "tokens",
    "completion_tokens": "tokens",
    "reasoning_tokens": "tokens",
    "cached_tokens": "tokens",
    "cost_usd": "usd",
    "requests": "requests",
    "billed_units": "units",
}
DEFAULT_USAGE_PROVIDER_CONFIG: dict[str, dict[str, Any]] = {
    "gemini": {
        "enabled": False,
        "alert_enabled": True,
        "prefer_provider_api": False,
        "metric_name": "total_tokens",
        "limit_value": 0.0,
        "threshold_percentages": list(DEFAULT_THRESHOLD_PERCENTAGES),
    },
    "deepseek": {
        "enabled": False,
        "alert_enabled": True,
        "prefer_provider_api": False,
        "metric_name": "total_tokens",
        "limit_value": 0.0,
        "threshold_percentages": list(DEFAULT_THRESHOLD_PERCENTAGES),
    },
    "grok": {
        "enabled": False,
        "alert_enabled": True,
        "prefer_provider_api": False,
        "metric_name": "total_tokens",
        "limit_value": 0.0,
        "threshold_percentages": list(DEFAULT_THRESHOLD_PERCENTAGES),
    },
    "github": {
        "enabled": False,
        "alert_enabled": True,
        "prefer_provider_api": False,
        "metric_name": "total_tokens",
        "limit_value": 0.0,
        "threshold_percentages": list(DEFAULT_THRESHOLD_PERCENTAGES),
        "billing_actor_type": "user",
        "billing_actor": "",
        "billing_token": None,
    },
    "copilot": {
        "enabled": False,
        "alert_enabled": True,
        "prefer_provider_api": False,
        "metric_name": "requests",
        "limit_value": 0.0,
        "threshold_percentages": list(DEFAULT_THRESHOLD_PERCENTAGES),
        "billing_actor_type": "user",
        "billing_actor": "",
        "billing_token": None,
    },
}


@dataclass(slots=True)
class UsagePeriod:
    key: str
    started_at: datetime
    ended_at: datetime


class LlmUsageService:
    def __init__(self, settings: Settings, runtime_config_loader: Callable[[], dict | None]) -> None:
        self.settings = settings
        self.runtime_config_loader = runtime_config_loader
        from .telegram import TelegramNotifier

        self.telegram = TelegramNotifier(settings)

    def record_usage(
        self,
        db: Session,
        *,
        provider: str,
        model: str | None,
        feature: str,
        usage: UsageMetrics | None,
        raw_payload: dict | None = None,
        metadata: dict | None = None,
    ) -> dict | None:
        provider_id = normalize_provider_name(provider)
        if provider_id not in USAGE_SUPPORTED_PROVIDERS:
            return None

        metrics = usage or UsageMetrics(requests=1)
        event = LlmUsageEvent(
            provider=provider_id,
            model=model,
            feature=feature,
            source="response_usage" if usage is not None else "request_only",
            requests=max(1, int(metrics.requests or 1)),
            prompt_tokens=int(metrics.prompt_tokens or 0),
            completion_tokens=int(metrics.completion_tokens or 0),
            total_tokens=int(metrics.total_tokens or 0),
            reasoning_tokens=int(metrics.reasoning_tokens or 0),
            cached_tokens=int(metrics.cached_tokens or 0),
            cost_usd=metrics.cost_usd,
            extra_metrics=dict(metrics.extra_metrics or {}),
            raw_payload={
                "usage": serialize_usage_metrics(metrics),
                "raw_payload": raw_payload or {},
                "metadata": metadata or {},
            },
        )
        db.add(event)
        db.flush()

        monitoring = self._monitoring_config().get(provider_id, {})
        period = current_usage_period()
        local_totals = self._aggregate_local_totals(db, provider_id, period)
        self._upsert_snapshot(
            db,
            provider=provider_id,
            source="local_events",
            period=period,
            totals=local_totals,
            preferred_metric=str(monitoring.get("metric_name", "total_tokens")),
            raw_payload={"kind": "local_aggregate"},
        )
        self._evaluate_thresholds(db, provider=provider_id, period=period, monitoring=monitoring, local_totals=local_totals)
        db.flush()
        return serialize_usage_event(event)

    def sync_provider_usage_once(self, provider: str | None = None) -> dict:
        init_db()
        monitoring_config = self._monitoring_config()
        target_providers = [normalize_provider_name(provider)] if provider else list(USAGE_SUPPORTED_PROVIDERS)
        results: list[dict[str, Any]] = []

        with SessionLocal() as db:
            for provider_id in target_providers:
                monitoring = monitoring_config.get(provider_id, {})
                try:
                    snapshot_result = self._fetch_remote_provider_snapshot(provider_id, monitoring)
                except Exception as exc:
                    results.append({"provider": provider_id, "ok": False, "reason": str(exc)})
                    continue

                if snapshot_result is None:
                    results.append({"provider": provider_id, "ok": False, "reason": "Provider API sync is not configured."})
                    continue

                period = current_usage_period()
                snapshot = self._upsert_snapshot(
                    db,
                    provider=provider_id,
                    source="provider_api",
                    period=period,
                    totals=snapshot_result["totals"],
                    preferred_metric=snapshot_result["primary_metric"],
                    raw_payload=snapshot_result["raw_payload"],
                )
                local_totals = self._aggregate_local_totals(db, provider_id, period)
                self._evaluate_thresholds(
                    db,
                    provider=provider_id,
                    period=period,
                    monitoring=monitoring,
                    local_totals=local_totals,
                    provider_snapshot=snapshot,
                )
                results.append(
                    {
                        "provider": provider_id,
                        "ok": True,
                        "source": "provider_api",
                        "primary_metric": snapshot.primary_metric,
                        "primary_value": snapshot.primary_value,
                        "synced_at": snapshot.synced_at.isoformat(),
                    }
                )
            db.commit()

        return {
            "period_key": current_usage_period().key,
            "results": results,
        }

    def summary_payload(self) -> dict:
        init_db()
        monitoring_config = self._monitoring_config()
        period = current_usage_period()

        with SessionLocal() as db:
            providers: list[dict[str, Any]] = []
            for provider_id in USAGE_SUPPORTED_PROVIDERS:
                monitoring = monitoring_config.get(provider_id, {})
                local_totals = self._aggregate_local_totals(db, provider_id, period)
                local_snapshot = db.execute(
                    select(LlmUsageSnapshot).where(
                        LlmUsageSnapshot.provider == provider_id,
                        LlmUsageSnapshot.source == "local_events",
                        LlmUsageSnapshot.period_key == period.key,
                    )
                ).scalar_one_or_none()
                provider_snapshot = db.execute(
                    select(LlmUsageSnapshot).where(
                        LlmUsageSnapshot.provider == provider_id,
                        LlmUsageSnapshot.source == "provider_api",
                        LlmUsageSnapshot.period_key == period.key,
                    )
                ).scalar_one_or_none()
                latest_alert = db.execute(
                    select(LlmUsageAlertEvent)
                    .where(
                        LlmUsageAlertEvent.provider == provider_id,
                        LlmUsageAlertEvent.period_key == period.key,
                    )
                    .order_by(LlmUsageAlertEvent.triggered_at.desc())
                    .limit(1)
                ).scalar_one_or_none()

                observed = self._resolve_observed_metric(
                    monitoring=monitoring,
                    local_totals=local_totals,
                    provider_snapshot=provider_snapshot,
                )
                limit_value = float(monitoring.get("limit_value", 0.0) or 0.0)
                percent_used = None
                if limit_value > 0 and observed["value"] is not None:
                    percent_used = round((float(observed["value"]) / limit_value) * 100, 2)

                providers.append(
                    {
                        "provider": provider_id,
                        "monitoring": serialize_monitoring_config(monitoring),
                        "observed": observed,
                        "limit_value": limit_value,
                        "percent_used": percent_used,
                        "local_totals": local_totals,
                        "provider_snapshot": serialize_usage_snapshot(provider_snapshot) if provider_snapshot else None,
                        "local_snapshot": serialize_usage_snapshot(local_snapshot) if local_snapshot else None,
                        "latest_alert": serialize_usage_alert(latest_alert) if latest_alert else None,
                    }
                )

        return {
            "period_key": period.key,
            "providers": providers,
        }

    def enabled_provider_ids(self) -> list[str]:
        return [
            provider_id
            for provider_id, config in self._monitoring_config().items()
            if bool(config.get("enabled"))
        ]

    def _monitoring_config(self) -> dict[str, dict[str, Any]]:
        runtime_config = self.runtime_config_loader() or {}
        raw_usage = runtime_config.get("usage_monitoring", {}) if isinstance(runtime_config, dict) else {}
        raw_providers = raw_usage.get("providers", {}) if isinstance(raw_usage, dict) else {}
        config: dict[str, dict[str, Any]] = {}
        for provider_id in USAGE_SUPPORTED_PROVIDERS:
            defaults = dict(DEFAULT_USAGE_PROVIDER_CONFIG.get(provider_id, {}))
            current = raw_providers.get(provider_id, {}) if isinstance(raw_providers, dict) else {}
            if not isinstance(current, dict):
                current = {}
            merged = {**defaults, **current}
            merged["threshold_percentages"] = normalize_threshold_percentages(merged.get("threshold_percentages"))
            merged["metric_name"] = normalize_metric_name(merged.get("metric_name"), provider_id)
            merged["limit_value"] = coerce_float(merged.get("limit_value"), 0.0)
            merged["enabled"] = bool(merged.get("enabled"))
            merged["alert_enabled"] = bool(merged.get("alert_enabled", True))
            merged["prefer_provider_api"] = bool(merged.get("prefer_provider_api", False))
            merged["billing_actor_type"] = normalize_actor_type(merged.get("billing_actor_type"))
            merged["billing_actor"] = str(merged.get("billing_actor", "") or "").strip()
            config[provider_id] = merged
        return config

    def _aggregate_local_totals(self, db: Session, provider: str, period: UsagePeriod) -> dict[str, float]:
        row = db.execute(
            select(
                func.coalesce(func.sum(LlmUsageEvent.requests), 0),
                func.coalesce(func.sum(LlmUsageEvent.prompt_tokens), 0),
                func.coalesce(func.sum(LlmUsageEvent.completion_tokens), 0),
                func.coalesce(func.sum(LlmUsageEvent.total_tokens), 0),
                func.coalesce(func.sum(LlmUsageEvent.reasoning_tokens), 0),
                func.coalesce(func.sum(LlmUsageEvent.cached_tokens), 0),
                func.coalesce(func.sum(LlmUsageEvent.cost_usd), 0.0),
            ).where(
                LlmUsageEvent.provider == provider,
                LlmUsageEvent.created_at >= period.started_at,
                LlmUsageEvent.created_at < period.ended_at,
            )
        ).one()
        return {
            "requests": float(row[0] or 0),
            "prompt_tokens": float(row[1] or 0),
            "completion_tokens": float(row[2] or 0),
            "total_tokens": float(row[3] or 0),
            "reasoning_tokens": float(row[4] or 0),
            "cached_tokens": float(row[5] or 0),
            "cost_usd": float(row[6] or 0.0),
        }

    def _upsert_snapshot(
        self,
        db: Session,
        *,
        provider: str,
        source: str,
        period: UsagePeriod,
        totals: dict[str, Any],
        preferred_metric: str,
        raw_payload: dict[str, Any],
    ) -> LlmUsageSnapshot:
        metric_name = normalize_metric_name(preferred_metric, provider)
        metric_unit = metric_unit_for_name(metric_name)
        primary_value = coerce_float(totals.get(metric_name), 0.0)
        if metric_name not in totals:
            metric_name, primary_value = first_non_empty_metric(totals)
            metric_unit = metric_unit_for_name(metric_name)

        snapshot = db.execute(
            select(LlmUsageSnapshot).where(
                LlmUsageSnapshot.provider == provider,
                LlmUsageSnapshot.source == source,
                LlmUsageSnapshot.period_key == period.key,
            )
        ).scalar_one_or_none()
        if snapshot is None:
            snapshot = LlmUsageSnapshot(
                provider=provider,
                source=source,
                period_key=period.key,
                period_started_at=period.started_at,
                period_ended_at=period.ended_at,
                primary_metric=metric_name,
                primary_unit=metric_unit,
                primary_value=primary_value,
                totals=totals,
                raw_payload=raw_payload,
                synced_at=utc_now(),
            )
            db.add(snapshot)
            db.flush()
            return snapshot

        snapshot.period_started_at = period.started_at
        snapshot.period_ended_at = period.ended_at
        snapshot.primary_metric = metric_name
        snapshot.primary_unit = metric_unit
        snapshot.primary_value = primary_value
        snapshot.totals = totals
        snapshot.raw_payload = raw_payload
        snapshot.synced_at = utc_now()
        db.flush()
        return snapshot

    def _evaluate_thresholds(
        self,
        db: Session,
        *,
        provider: str,
        period: UsagePeriod,
        monitoring: dict[str, Any],
        local_totals: dict[str, float],
        provider_snapshot: LlmUsageSnapshot | None = None,
    ) -> None:
        if not monitoring.get("enabled") or not monitoring.get("alert_enabled"):
            return

        limit_value = float(monitoring.get("limit_value", 0.0) or 0.0)
        if limit_value <= 0:
            return

        observed = self._resolve_observed_metric(
            monitoring=monitoring,
            local_totals=local_totals,
            provider_snapshot=provider_snapshot,
        )
        if observed["value"] is None:
            return

        percent_used = (float(observed["value"]) / limit_value) * 100
        thresholds = normalize_threshold_percentages(monitoring.get("threshold_percentages"))
        if not thresholds:
            return

        for threshold in thresholds:
            if percent_used < threshold:
                continue
            existing = db.execute(
                select(LlmUsageAlertEvent).where(
                    LlmUsageAlertEvent.provider == provider,
                    LlmUsageAlertEvent.period_key == period.key,
                    LlmUsageAlertEvent.metric_name == observed["metric_name"],
                    LlmUsageAlertEvent.threshold_percent == threshold,
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue

            alert = LlmUsageAlertEvent(
                provider=provider,
                period_key=period.key,
                metric_name=observed["metric_name"],
                metric_unit=observed["metric_unit"],
                source=observed["source"],
                threshold_percent=threshold,
                limit_value=limit_value,
                observed_value=float(observed["value"]),
                status="pending",
                raw_payload={
                    "percent_used": round(percent_used, 2),
                    "local_totals": local_totals,
                    "provider_snapshot": serialize_usage_snapshot(provider_snapshot) if provider_snapshot else None,
                },
            )
            db.add(alert)
            db.flush()
            self._deliver_alert(db, alert)

    def _deliver_alert(self, db: Session, alert: LlmUsageAlertEvent) -> None:
        if not self.telegram.available:
            alert.status = "skipped"
            alert.error_message = "Telegram notifier is not configured."
            return

        percent_used = round((alert.observed_value / max(alert.limit_value, 1e-9)) * 100, 2)
        title = "LLM Usage Alert"
        body_lines = [
            f"Provider: {alert.provider}",
            f"Period: {alert.period_key}",
            f"Metric: {alert.metric_name}",
            f"Used: {format_metric_value(alert.observed_value, alert.metric_unit)} / {format_metric_value(alert.limit_value, alert.metric_unit)}",
            f"Usage: {percent_used:.2f}%",
            f"Threshold: {alert.threshold_percent:.0f}%",
            f"Source: {alert.source}",
        ]
        try:
            deliveries = self.telegram.send_system_notice(
                db,
                title=title,
                body_lines=body_lines,
                metadata={
                    "kind": "llm_usage_threshold",
                    "provider": alert.provider,
                    "period_key": alert.period_key,
                    "metric_name": alert.metric_name,
                    "threshold_percent": alert.threshold_percent,
                },
            )
            alert.status = "sent"
            alert.telegram_delivery_count = deliveries
            alert.delivered_at = utc_now()
        except Exception as exc:
            alert.status = "failed"
            alert.error_message = str(exc)

    def _resolve_observed_metric(
        self,
        *,
        monitoring: dict[str, Any],
        local_totals: dict[str, float],
        provider_snapshot: LlmUsageSnapshot | None,
    ) -> dict[str, Any]:
        metric_name = normalize_metric_name(monitoring.get("metric_name"), "")
        metric_unit = metric_unit_for_name(metric_name)
        prefer_provider_api = bool(monitoring.get("prefer_provider_api"))
        provider_totals = provider_snapshot.totals if provider_snapshot is not None else {}

        value = None
        source = "local_events"
        if prefer_provider_api and metric_name in provider_totals:
            value = coerce_float(provider_totals.get(metric_name), None)
            source = "provider_api"
        elif metric_name in local_totals:
            value = coerce_float(local_totals.get(metric_name), None)
        elif metric_name in provider_totals:
            value = coerce_float(provider_totals.get(metric_name), None)
            source = "provider_api"
        return {
            "metric_name": metric_name,
            "metric_unit": metric_unit,
            "value": value,
            "source": source,
        }

    def _fetch_remote_provider_snapshot(self, provider: str, monitoring: dict[str, Any]) -> dict[str, Any] | None:
        if provider not in {"github", "copilot"}:
            return None

        token = str(monitoring.get("billing_token") or "").strip()
        if not token:
            token = self.settings.github_models_token or ""
        actor_type = normalize_actor_type(monitoring.get("billing_actor_type"))
        actor = str(monitoring.get("billing_actor") or "").strip()
        if not token or not actor:
            return None

        now = utc_now()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": self.settings.github_models_api_version,
        }
        if actor_type == "org":
            base = f"https://api.github.com/organizations/{actor}/settings/billing"
        else:
            base = f"https://api.github.com/users/{actor}/settings/billing"

        if provider == "github":
            response = requests.get(
                f"{base}/usage/summary",
                headers=headers,
                params={"year": now.year, "month": now.month, "product": "Models"},
                timeout=self.settings.analysis_timeout_seconds,
            )
            if not response.ok:
                raise RuntimeError(f"GitHub billing API error {response.status_code}: {response.text[:500]}")
            payload = response.json()
            usage_items = payload.get("usageItems", [])
            totals = summarize_github_billing_usage(usage_items, mode="models")
            return {
                "primary_metric": "cost_usd",
                "totals": totals,
                "raw_payload": payload,
            }

        response = requests.get(
            f"{base}/premium_request/usage",
            headers=headers,
            params={"year": now.year, "month": now.month},
            timeout=self.settings.analysis_timeout_seconds,
        )
        if not response.ok:
            raise RuntimeError(f"GitHub Copilot billing API error {response.status_code}: {response.text[:500]}")
        payload = response.json()
        usage_items = payload.get("usageItems", [])
        totals = summarize_github_billing_usage(usage_items, mode="copilot")
        return {
            "primary_metric": "requests",
            "totals": totals,
            "raw_payload": payload,
        }


def current_usage_period(now: datetime | None = None) -> UsagePeriod:
    current = now or utc_now()
    current = current.astimezone(timezone.utc)
    started_at = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if started_at.month == 12:
        ended_at = started_at.replace(year=started_at.year + 1, month=1)
    else:
        ended_at = started_at.replace(month=started_at.month + 1)
    return UsagePeriod(
        key=f"{started_at.year:04d}-{started_at.month:02d}",
        started_at=started_at,
        ended_at=ended_at,
    )


def usage_monitoring_defaults() -> dict[str, Any]:
    return {
        "providers": {
            provider_id: serialize_monitoring_config(config)
            for provider_id, config in DEFAULT_USAGE_PROVIDER_CONFIG.items()
        }
    }


def normalize_threshold_percentages(raw: Any) -> list[float]:
    if isinstance(raw, str):
        parts = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list):
        parts = raw
    else:
        parts = list(DEFAULT_THRESHOLD_PERCENTAGES)

    values: list[float] = []
    for part in parts:
        try:
            value = float(part)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        values.append(value)
    unique = sorted(set(values))
    return unique or list(DEFAULT_THRESHOLD_PERCENTAGES)


def normalize_metric_name(value: Any, provider: str) -> str:
    text = str(value or "").strip()
    if text in METRIC_UNIT_BY_NAME:
        return text
    return DEFAULT_USAGE_PROVIDER_CONFIG.get(provider, {}).get("metric_name", "total_tokens")


def normalize_actor_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "org" if text == "org" else "user"


def metric_unit_for_name(metric_name: str) -> str:
    return METRIC_UNIT_BY_NAME.get(metric_name, "units")


def first_non_empty_metric(totals: dict[str, Any]) -> tuple[str, float]:
    for key in ("total_tokens", "requests", "cost_usd", "billed_units"):
        value = coerce_float(totals.get(key), None)
        if value is not None:
            return key, value
    return "requests", 0.0


def summarize_github_billing_usage(usage_items: Any, *, mode: str) -> dict[str, float]:
    items = usage_items if isinstance(usage_items, list) else []
    filtered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        product = str(item.get("product", "") or "").strip().lower()
        sku = str(item.get("sku", "") or "").strip().lower()
        if mode == "models":
            if "model" not in product and "model" not in sku:
                continue
        if mode == "copilot":
            if "copilot" not in product and "copilot" not in sku:
                continue
        filtered.append(item)

    cost_usd = 0.0
    units = 0.0
    for item in filtered:
        cost_usd += coerce_float(item.get("netAmount"), coerce_float(item.get("grossAmount"), 0.0))
        units += coerce_float(item.get("netQuantity"), coerce_float(item.get("quantity"), coerce_float(item.get("grossQuantity"), 0.0)))

    totals = {"cost_usd": cost_usd}
    if mode == "copilot":
        totals["requests"] = units
    else:
        totals["billed_units"] = units
    return totals


def serialize_usage_metrics(metrics: UsageMetrics | None) -> dict[str, Any] | None:
    if metrics is None:
        return None
    return {
        "requests": metrics.requests,
        "prompt_tokens": metrics.prompt_tokens,
        "completion_tokens": metrics.completion_tokens,
        "total_tokens": metrics.total_tokens,
        "reasoning_tokens": metrics.reasoning_tokens,
        "cached_tokens": metrics.cached_tokens,
        "cost_usd": metrics.cost_usd,
        "extra_metrics": metrics.extra_metrics or {},
    }


def serialize_usage_event(event: LlmUsageEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "id": event.id,
        "provider": event.provider,
        "model": event.model,
        "feature": event.feature,
        "source": event.source,
        "requests": event.requests,
        "prompt_tokens": event.prompt_tokens,
        "completion_tokens": event.completion_tokens,
        "total_tokens": event.total_tokens,
        "reasoning_tokens": event.reasoning_tokens,
        "cached_tokens": event.cached_tokens,
        "cost_usd": event.cost_usd,
        "extra_metrics": event.extra_metrics,
        "created_at": event.created_at.isoformat(),
    }


def serialize_usage_snapshot(snapshot: LlmUsageSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "provider": snapshot.provider,
        "source": snapshot.source,
        "period_key": snapshot.period_key,
        "primary_metric": snapshot.primary_metric,
        "primary_unit": snapshot.primary_unit,
        "primary_value": snapshot.primary_value,
        "totals": snapshot.totals,
        "synced_at": snapshot.synced_at.isoformat(),
    }


def serialize_usage_alert(alert: LlmUsageAlertEvent | None) -> dict[str, Any] | None:
    if alert is None:
        return None
    return {
        "provider": alert.provider,
        "period_key": alert.period_key,
        "metric_name": alert.metric_name,
        "metric_unit": alert.metric_unit,
        "source": alert.source,
        "threshold_percent": alert.threshold_percent,
        "limit_value": alert.limit_value,
        "observed_value": alert.observed_value,
        "status": alert.status,
        "telegram_delivery_count": alert.telegram_delivery_count,
        "error_message": alert.error_message,
        "triggered_at": alert.triggered_at.isoformat(),
        "delivered_at": alert.delivered_at.isoformat() if alert.delivered_at else None,
    }


def serialize_monitoring_config(config: dict[str, Any]) -> dict[str, Any]:
    metric_name = normalize_metric_name(config.get("metric_name"), "")
    return {
        "enabled": bool(config.get("enabled")),
        "alert_enabled": bool(config.get("alert_enabled", True)),
        "prefer_provider_api": bool(config.get("prefer_provider_api", False)),
        "metric_name": metric_name,
        "metric_unit": metric_unit_for_name(metric_name),
        "limit_value": coerce_float(config.get("limit_value"), 0.0),
        "threshold_percentages": normalize_threshold_percentages(config.get("threshold_percentages")),
        "billing_actor_type": normalize_actor_type(config.get("billing_actor_type")),
        "billing_actor": str(config.get("billing_actor", "") or "").strip(),
        "has_billing_token": bool(config.get("billing_token")),
        "billing_token_masked": mask_secret(config.get("billing_token")),
    }


def mask_secret(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def format_metric_value(value: float, unit: str) -> str:
    if unit == "usd":
        return f"${value:,.2f}"
    if unit == "tokens":
        return f"{int(value):,} tokens"
    if unit == "requests":
        return f"{int(value):,} requests"
    return f"{value:,.2f} {unit}"


def coerce_float(value: Any, default: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
