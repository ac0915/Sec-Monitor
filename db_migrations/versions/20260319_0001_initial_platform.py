"""initial platform schema

Revision ID: 20260319_0001
Revises: 
Create Date: 2026-03-19 00:00:01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260319_0001"
down_revision = None
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
        "ingestion_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("entries_seen", sa.Integer(), nullable=False),
        sa.Column("matched_entries", sa.Integer(), nullable=False),
        sa.Column("new_filings", sa.Integer(), nullable=False),
        sa.Column("updated_filings", sa.Integer(), nullable=False),
        sa.Column("analyzed_filings", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text()),
    )

    _create_table_if_missing(
        "filings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("accession_number", sa.String(length=64)),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("form_type", sa.String(length=50), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("sec_items", sa.JSON(), nullable=False),
        sa.Column("raw_feed_summary", sa.Text()),
        sa.Column("entry_link", sa.String(length=1000), nullable=False),
        sa.Column("primary_document_url", sa.String(length=1000)),
        sa.Column("raw_document_text", sa.Text()),
        sa.Column("document_char_count", sa.Integer(), nullable=False),
        sa.Column("analysis_status", sa.String(length=32), nullable=False),
        sa.Column("analysis_error", sa.Text()),
        sa.Column("telegram_status", sa.String(length=255), nullable=False),
        sa.Column("telegram_sent_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entry_link", name="uq_filings_entry_link"),
    )
    _create_index_if_missing("ix_filings_accession_number", "filings", ["accession_number"])
    _create_index_if_missing("ix_filings_ticker", "filings", ["ticker"])
    _create_index_if_missing("ix_filings_form_type", "filings", ["form_type"])
    _create_index_if_missing("ix_filings_tier", "filings", ["tier"])
    _create_index_if_missing("ix_filings_entry_link", "filings", ["entry_link"], unique=True)
    _create_index_if_missing("ix_filings_published_at", "filings", ["published_at"])

    _create_table_if_missing(
        "analysis_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filing_id", sa.Integer(), sa.ForeignKey("filings.id"), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("impact_label", sa.String(length=16), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("key_takeaways", sa.JSON(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("filing_id", name="uq_analysis_results_filing_id"),
    )

    _create_table_if_missing(
        "filing_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filing_id", sa.Integer(), sa.ForeignKey("filings.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("chunk_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_filing_chunks_filing_id", "filing_chunks", ["filing_id"])

    _create_table_if_missing(
        "telegram_chats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=False),
        sa.Column("chat_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255)),
        sa.Column("username", sa.String(length=255)),
        sa.Column("first_name", sa.String(length=255)),
        sa.Column("last_name", sa.String(length=255)),
        sa.Column("language_code", sa.String(length=32)),
        sa.Column("alerts_enabled", sa.Boolean(), nullable=False),
        sa.Column("assistant_enabled", sa.Boolean(), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True)),
        sa.Column("last_outbound_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("telegram_chat_id", name="uq_telegram_chats_telegram_chat_id"),
    )
    _create_index_if_missing("ix_telegram_chats_telegram_chat_id", "telegram_chats", ["telegram_chat_id"], unique=True)

    _create_table_if_missing(
        "telegram_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), sa.ForeignKey("telegram_chats.id"), nullable=False),
        sa.Column("telegram_update_id", sa.String(length=64)),
        sa.Column("telegram_message_id", sa.String(length=64)),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("command_name", sa.String(length=64)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("model", sa.String(length=128)),
        sa.Column("message_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("telegram_update_id", name="uq_telegram_messages_telegram_update_id"),
    )
    _create_index_if_missing("ix_telegram_messages_chat_id", "telegram_messages", ["chat_id"])

    _create_table_if_missing(
        "telegram_state",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    _create_table_if_missing(
        "app_config_entries",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    _create_table_if_missing(
        "filing_chunk_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chunk_id", sa.Integer(), sa.ForeignKey("filing_chunks.id"), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("vector", sa.JSON()),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chunk_id", "provider", "model", name="uq_chunk_embedding_provider_model"),
    )
    _create_index_if_missing("ix_filing_chunk_embeddings_chunk_id", "filing_chunk_embeddings", ["chunk_id"])

    _create_table_if_missing(
        "admin_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("username", name="uq_admin_users_username"),
    )
    _create_index_if_missing("ix_admin_users_username", "admin_users", ["username"], unique=True)

    _create_table_if_missing(
        "admin_api_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=120)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("token_hash", name="uq_admin_api_tokens_token_hash"),
    )
    _create_index_if_missing("ix_admin_api_tokens_user_id", "admin_api_tokens", ["user_id"])
    _create_index_if_missing("ix_admin_api_tokens_token_hash", "admin_api_tokens", ["token_hash"], unique=True)

    _create_table_if_missing(
        "admin_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("admin_users.id")),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=120)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("event_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_admin_audit_events_user_id", "admin_audit_events", ["user_id"])

    _create_table_if_missing(
        "price_ingestion_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_tickers", sa.Integer(), nullable=False),
        sa.Column("successful_tickers", sa.Integer(), nullable=False),
        sa.Column("failed_tickers", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    _create_table_if_missing(
        "price_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=16)),
        sa.Column("exchange", sa.String(length=64)),
        sa.Column("market_state", sa.String(length=32)),
        sa.Column("regular_market_price", sa.Float()),
        sa.Column("previous_close", sa.Float()),
        sa.Column("change_amount", sa.Float()),
        sa.Column("change_percent", sa.Float()),
        sa.Column("day_low", sa.Float()),
        sa.Column("day_high", sa.Float()),
        sa.Column("volume", sa.Integer()),
        sa.Column("market_cap", sa.Float()),
        sa.Column("source_payload", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_price_snapshots_ticker", "price_snapshots", ["ticker"])
    _create_index_if_missing("ix_price_snapshots_fetched_at", "price_snapshots", ["fetched_at"])

    _create_table_if_missing(
        "label_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filing_id", sa.Integer(), sa.ForeignKey("filings.id")),
        sa.Column("analysis_result_id", sa.Integer(), sa.ForeignKey("analysis_results.id")),
        sa.Column("chunk_id", sa.Integer(), sa.ForeignKey("filing_chunks.id")),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("task_metadata", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("admin_users.id")),
        sa.Column("assigned_to_user_id", sa.Integer(), sa.ForeignKey("admin_users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    _create_index_if_missing("ix_label_tasks_filing_id", "label_tasks", ["filing_id"])
    _create_index_if_missing("ix_label_tasks_analysis_result_id", "label_tasks", ["analysis_result_id"])
    _create_index_if_missing("ix_label_tasks_chunk_id", "label_tasks", ["chunk_id"])

    _create_table_if_missing(
        "label_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("label_tasks.id"), nullable=False),
        sa.Column("filing_id", sa.Integer(), sa.ForeignKey("filings.id")),
        sa.Column("analysis_result_id", sa.Integer(), sa.ForeignKey("analysis_results.id")),
        sa.Column("chunk_id", sa.Integer(), sa.ForeignKey("filing_chunks.id")),
        sa.Column("labeler_user_id", sa.Integer(), sa.ForeignKey("admin_users.id")),
        sa.Column("label_type", sa.String(length=64), nullable=False),
        sa.Column("impact_label", sa.String(length=16)),
        sa.Column("relevance_label", sa.String(length=32)),
        sa.Column("summary_text", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("label_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_label_records_task_id", "label_records", ["task_id"])
    _create_index_if_missing("ix_label_records_filing_id", "label_records", ["filing_id"])
    _create_index_if_missing("ix_label_records_analysis_result_id", "label_records", ["analysis_result_id"])
    _create_index_if_missing("ix_label_records_chunk_id", "label_records", ["chunk_id"])
    _create_index_if_missing("ix_label_records_labeler_user_id", "label_records", ["labeler_user_id"])

    _create_table_if_missing(
        "evaluation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("label_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_examples", sa.Integer(), nullable=False),
        sa.Column("completed_examples", sa.Integer(), nullable=False),
        sa.Column("impact_accuracy", sa.Float()),
        sa.Column("summary_similarity_mean", sa.Float()),
        sa.Column("error_message", sa.Text()),
        sa.Column("run_metadata", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("admin_users.id")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    _create_table_if_missing(
        "evaluation_examples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evaluation_run_id", sa.Integer(), sa.ForeignKey("evaluation_runs.id"), nullable=False),
        sa.Column("filing_id", sa.Integer(), sa.ForeignKey("filings.id"), nullable=False),
        sa.Column("label_record_id", sa.Integer(), sa.ForeignKey("label_records.id"), nullable=False),
        sa.Column("predicted_impact", sa.String(length=16)),
        sa.Column("predicted_summary", sa.Text()),
        sa.Column("predicted_takeaways", sa.JSON(), nullable=False),
        sa.Column("impact_match", sa.Boolean()),
        sa.Column("summary_similarity", sa.Float()),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_evaluation_examples_evaluation_run_id", "evaluation_examples", ["evaluation_run_id"])
    _create_index_if_missing("ix_evaluation_examples_filing_id", "evaluation_examples", ["filing_id"])
    _create_index_if_missing("ix_evaluation_examples_label_record_id", "evaluation_examples", ["label_record_id"])


def downgrade() -> None:
    for table_name in [
        "evaluation_examples",
        "evaluation_runs",
        "label_records",
        "label_tasks",
        "price_snapshots",
        "price_ingestion_runs",
        "admin_audit_events",
        "admin_api_tokens",
        "admin_users",
        "filing_chunk_embeddings",
        "app_config_entries",
        "telegram_state",
        "telegram_messages",
        "telegram_chats",
        "filing_chunks",
        "analysis_results",
        "filings",
        "ingestion_runs",
    ]:
        if table_name in _table_names():
            op.drop_table(table_name)
