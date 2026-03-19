from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
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
