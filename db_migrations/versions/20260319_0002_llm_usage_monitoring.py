"""add llm usage monitoring tables

Revision ID: 20260319_0002
Revises: 20260319_0001
Create Date: 2026-03-19 15:05:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260319_0002"
down_revision = "20260319_0001"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {item["name"] for item in inspector.get_indexes(table_name)}


def _create_table_if_missing(name: str, *columns, **kwargs) -> None:
    if name in _table_names():
        return
    op.create_table(name, *columns, **kwargs)


def _create_index_if_missing(name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if table_name not in _table_names():
        return
    if name in _index_names(table_name):
        return
    op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    _create_table_if_missing(
        "llm_usage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128)),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float()),
        sa.Column("extra_metrics", sa.JSON(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_llm_usage_events_provider", "llm_usage_events", ["provider"])
    _create_index_if_missing("ix_llm_usage_events_created_at", "llm_usage_events", ["created_at"])

    _create_table_if_missing(
        "llm_usage_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("period_key", sa.String(length=16), nullable=False),
        sa.Column("period_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_ended_at", sa.DateTime(timezone=True)),
        sa.Column("primary_metric", sa.String(length=64), nullable=False),
        sa.Column("primary_unit", sa.String(length=32), nullable=False),
        sa.Column("primary_value", sa.Float(), nullable=False),
        sa.Column("totals", sa.JSON(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "source", "period_key", name="uq_llm_usage_snapshots_provider_source_period"),
    )
    _create_index_if_missing("ix_llm_usage_snapshots_provider", "llm_usage_snapshots", ["provider"])
    _create_index_if_missing("ix_llm_usage_snapshots_period_key", "llm_usage_snapshots", ["period_key"])
    _create_index_if_missing("ix_llm_usage_snapshots_synced_at", "llm_usage_snapshots", ["synced_at"])

    _create_table_if_missing(
        "llm_usage_alert_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("period_key", sa.String(length=16), nullable=False),
        sa.Column("metric_name", sa.String(length=64), nullable=False),
        sa.Column("metric_unit", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("threshold_percent", sa.Float(), nullable=False),
        sa.Column("limit_value", sa.Float(), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("telegram_delivery_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "provider",
            "period_key",
            "metric_name",
            "threshold_percent",
            name="uq_llm_usage_alert_events_provider_period_metric_threshold",
        ),
    )
    _create_index_if_missing("ix_llm_usage_alert_events_provider", "llm_usage_alert_events", ["provider"])
    _create_index_if_missing("ix_llm_usage_alert_events_period_key", "llm_usage_alert_events", ["period_key"])


def downgrade() -> None:
    for table_name in [
        "llm_usage_alert_events",
        "llm_usage_snapshots",
        "llm_usage_events",
    ]:
        if table_name in _table_names():
            op.drop_table(table_name)
