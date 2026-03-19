from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    entries_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matched_entries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_filings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_filings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    analyzed_filings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(24), default="sec", nullable=False)
    accession_number: Mapped[str | None] = mapped_column(String(64), index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    form_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    tier: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    sec_items: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    raw_feed_summary: Mapped[str | None] = mapped_column(Text)
    entry_link: Mapped[str] = mapped_column(String(1000), unique=True, index=True, nullable=False)
    primary_document_url: Mapped[str | None] = mapped_column(String(1000))
    raw_document_text: Mapped[str | None] = mapped_column(Text)
    document_char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    analysis_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    analysis_error: Mapped[str | None] = mapped_column(Text)
    telegram_status: Mapped[str] = mapped_column(String(255), default="pending", nullable=False)
    telegram_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    analysis: Mapped[AnalysisResult | None] = relationship(
        back_populates="filing",
        uselist=False,
        cascade="all, delete-orphan",
    )
    chunks: Mapped[list[FilingChunk]] = relationship(
        back_populates="filing",
        cascade="all, delete-orphan",
        order_by="FilingChunk.chunk_index",
    )


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="zh-Hant", nullable=False)
    impact_label: Mapped[str] = mapped_column(String(16), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    key_takeaways: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    filing: Mapped[Filing] = relationship(back_populates="analysis")


class FilingChunk(Base):
    __tablename__ = "filing_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), index=True, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), default="llm_corpus", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    filing: Mapped[Filing] = relationship(back_populates="chunks")


class FilingChunkEmbedding(Base):
    __tablename__ = "filing_chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "provider", "model", name="uq_chunk_embedding_provider_model"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("filing_chunks.id"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    vector: Mapped[list[float] | None] = mapped_column(JSON)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    chunk: Mapped[FilingChunk] = relationship()


class TelegramChat(Base):
    __tablename__ = "telegram_chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    chat_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    language_code: Mapped[str | None] = mapped_column(String(32))
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assistant_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list[TelegramMessage]] = relationship(
        back_populates="chat",
        cascade="all, delete-orphan",
        order_by="TelegramMessage.created_at",
    )


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("telegram_chats.id"), index=True, nullable=False)
    telegram_update_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    telegram_message_id: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    command_name: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    message_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    chat: Mapped[TelegramChat] = relationship(back_populates="messages")


class TelegramState(Base):
    __tablename__ = "telegram_state"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class AppConfigEntry(Base):
    __tablename__ = "app_config_entries"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="admin", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminApiToken(Base):
    __tablename__ = "admin_api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[AdminUser] = relationship()


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    event_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[AdminUser | None] = relationship()


class PriceIngestionRun(Base):
    __tablename__ = "price_ingestion_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    requested_tickers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successful_tickers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_tickers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(16))
    exchange: Mapped[str | None] = mapped_column(String(64))
    market_state: Mapped[str | None] = mapped_column(String(32))
    regular_market_price: Mapped[float | None] = mapped_column(Float)
    previous_close: Mapped[float | None] = mapped_column(Float)
    change_amount: Mapped[float | None] = mapped_column(Float)
    change_percent: Mapped[float | None] = mapped_column(Float)
    day_low: Mapped[float | None] = mapped_column(Float)
    day_high: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(Integer)
    market_cap: Mapped[float | None] = mapped_column(Float)
    source_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True, nullable=False)


class LabelTask(Base):
    __tablename__ = "label_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int | None] = mapped_column(ForeignKey("filings.id"), index=True)
    analysis_result_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_results.id"), index=True)
    chunk_id: Mapped[int | None] = mapped_column(ForeignKey("filing_chunks.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(64), default="filing_assessment", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    task_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"))
    assigned_to_user_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    filing: Mapped[Filing | None] = relationship()
    analysis_result: Mapped[AnalysisResult | None] = relationship()
    chunk: Mapped[FilingChunk | None] = relationship()
    created_by: Mapped[AdminUser | None] = relationship(foreign_keys=[created_by_user_id])
    assigned_to: Mapped[AdminUser | None] = relationship(foreign_keys=[assigned_to_user_id])
    labels: Mapped[list[LabelRecord]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="LabelRecord.created_at",
    )


class LabelRecord(Base):
    __tablename__ = "label_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("label_tasks.id"), index=True, nullable=False)
    filing_id: Mapped[int | None] = mapped_column(ForeignKey("filings.id"), index=True)
    analysis_result_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_results.id"), index=True)
    chunk_id: Mapped[int | None] = mapped_column(ForeignKey("filing_chunks.id"), index=True)
    labeler_user_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), index=True)
    label_type: Mapped[str] = mapped_column(String(64), default="filing_assessment", nullable=False)
    impact_label: Mapped[str | None] = mapped_column(String(16))
    relevance_label: Mapped[str | None] = mapped_column(String(32))
    summary_text: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    label_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    task: Mapped[LabelTask] = relationship(back_populates="labels")
    filing: Mapped[Filing | None] = relationship()
    analysis_result: Mapped[AnalysisResult | None] = relationship()
    chunk: Mapped[FilingChunk | None] = relationship()
    labeler: Mapped[AdminUser | None] = relationship()


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    label_type: Mapped[str] = mapped_column(String(64), default="filing_assessment", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    total_examples: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_examples: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    impact_accuracy: Mapped[float | None] = mapped_column(Float)
    summary_similarity_mean: Mapped[float | None] = mapped_column(Float)
    error_message: Mapped[str | None] = mapped_column(Text)
    run_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by: Mapped[AdminUser | None] = relationship()
    examples: Mapped[list[EvaluationExample]] = relationship(
        back_populates="evaluation_run",
        cascade="all, delete-orphan",
        order_by="EvaluationExample.id",
    )


class EvaluationExample(Base):
    __tablename__ = "evaluation_examples"

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_run_id: Mapped[int] = mapped_column(ForeignKey("evaluation_runs.id"), index=True, nullable=False)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), index=True, nullable=False)
    label_record_id: Mapped[int] = mapped_column(ForeignKey("label_records.id"), index=True, nullable=False)
    predicted_impact: Mapped[str | None] = mapped_column(String(16))
    predicted_summary: Mapped[str | None] = mapped_column(Text)
    predicted_takeaways: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    impact_match: Mapped[bool | None] = mapped_column(Boolean)
    summary_similarity: Mapped[float | None] = mapped_column(Float)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    evaluation_run: Mapped[EvaluationRun] = relationship(back_populates="examples")
    filing: Mapped[Filing] = relationship()
    label_record: Mapped[LabelRecord] = relationship()


class LlmUsageEvent(Base):
    __tablename__ = "llm_usage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    requests: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    extra_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True, nullable=False)


class LlmUsageSnapshot(Base):
    __tablename__ = "llm_usage_snapshots"
    __table_args__ = (
        UniqueConstraint("provider", "source", "period_key", name="uq_llm_usage_snapshots_provider_source_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    period_key: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    period_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    primary_metric: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    primary_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    totals: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True, nullable=False)


class LlmUsageAlertEvent(Base):
    __tablename__ = "llm_usage_alert_events"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "period_key",
            "metric_name",
            "threshold_percent",
            name="uq_llm_usage_alert_events_provider_period_metric_threshold",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    period_key: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold_percent: Mapped[float] = mapped_column(Float, nullable=False)
    limit_value: Mapped[float] = mapped_column(Float, nullable=False)
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    telegram_delivery_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
