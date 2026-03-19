from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .database import SessionLocal, init_db
from .models import (
    AnalysisResult,
    EvaluationRun,
    Filing,
    FilingChunk,
    FilingChunkEmbedding,
    IngestionRun,
    LabelTask,
    PriceIngestionRun,
    PriceSnapshot,
    TelegramChat,
    utc_now,
)
from .services.analysis import build_analysis_service
from .services.auth import AuthPrincipal
from .services.embeddings import build_embedding_service
from .services.evaluation import EvaluationService
from .services.gui_settings import GuiSettingsService
from .services.intelligence import (
    build_feed_facets,
    build_sec_brief,
    build_signal_index,
    build_theme_clusters,
    classify_filing_themes,
    clean_text,
    compute_filing_signal_score,
    filter_feed_filings,
)
from .services.llm_usage import LlmUsageService
from .services.labeling import LabelingService
from .services.prices import build_price_service
from .services.sec_client import FilingCandidate, SECClient
from .services.telegram import TelegramAssistantService, TelegramNotifier
from .watchlist import WATCHLIST


logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    normalized = re.sub(r"\s+\n", "\n", text)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    length = len(normalized)

    while start < length:
        end = min(length, start + chunk_size)
        if end < length:
            split_point = normalized.rfind(" ", start + chunk_size // 2, end)
            if split_point > start:
                end = split_point

        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= length:
            break
        start = max(end - overlap, start + 1)

    return chunks


class IngestionService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.gui_settings = GuiSettingsService(self.settings)
        self.effective_settings = self.settings
        self.analysis_runtime_source = "env"
        self.analysis_runtime_error: str | None = None
        self.sec_client = SECClient(self.settings)
        self.analysis_service = build_analysis_service(self.settings)
        self.embedding_service = build_embedding_service(self.settings)
        self.price_service = build_price_service(self.settings)
        self.llm_usage_service = LlmUsageService(self.settings, self.gui_settings.load_runtime_config)
        self.telegram = TelegramNotifier(self.settings)
        self.telegram_assistant = TelegramAssistantService(
            self.settings,
            analysis_service=self.analysis_service,
            usage_service=self.llm_usage_service,
        )
        self.labeling_service = LabelingService(self.settings)
        self.evaluation_service = EvaluationService(self.settings, self.analysis_service, self.llm_usage_service)
        self._refresh_runtime_services()

    def ingest_once(self) -> dict:
        self._refresh_runtime_services()
        init_db()
        with SessionLocal() as db:
            run = IngestionRun(status="running")
            db.add(run)
            db.commit()
            db.refresh(run)

            try:
                entry_count, candidates = self.sec_client.fetch_current_candidates()
                run.entries_seen = entry_count
                run.matched_entries = len(candidates)
                db.commit()

                stats = {
                    "entries_seen": entry_count,
                    "matched_entries": len(candidates),
                    "new_filings": 0,
                    "updated_filings": 0,
                    "analyzed_filings": 0,
                    "price_quotes": 0,
                    "price_sync_error": None,
                }

                for candidate in candidates:
                    created = self._upsert_filing(db, candidate)
                    if created:
                        stats["new_filings"] += 1
                    else:
                        stats["updated_filings"] += 1

                    filing = db.execute(
                        select(Filing)
                        .options(selectinload(Filing.analysis), selectinload(Filing.chunks))
                        .where(Filing.entry_link == candidate.entry_link)
                    ).scalar_one()

                    should_fetch_text = self.settings.fetch_full_text_for_all or candidate.tier in self.settings.analyze_tiers
                    if should_fetch_text and not filing.raw_document_text:
                        self._hydrate_document_and_chunks(db, filing)

                    if (
                        filing.raw_document_text
                        and filing.tier in self.settings.analyze_tiers
                        and filing.analysis is None
                        and self.analysis_service.available
                    ):
                        if self._run_analysis(db, filing):
                            stats["analyzed_filings"] += 1

                    if (
                        filing.tier in self.settings.telegram_tiers
                        and self.telegram.available
                        and filing.telegram_status != "sent"
                    ):
                        self._send_telegram(db, filing)

                    db.commit()

                if self.settings.auto_sync_prices_on_ingest and self.price_service.available:
                    try:
                        price_stats = self.sync_prices_once()
                        stats["price_quotes"] = price_stats["stored_quotes"]
                    except Exception as exc:
                        logger.warning("Price sync failed during ingestion: %s", exc)
                        stats["price_sync_error"] = str(exc)

                run.status = "completed"
                run.new_filings = stats["new_filings"]
                run.updated_filings = stats["updated_filings"]
                run.analyzed_filings = stats["analyzed_filings"]
                run.completed_at = utc_now()
                db.commit()
                return stats
            except Exception as exc:
                logger.exception("Ingestion run failed")
                run.status = "failed"
                run.error_message = str(exc)
                run.completed_at = utc_now()
                db.commit()
                raise

    def export_llm_dataset(self, output_path: Path) -> int:
        init_db()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with SessionLocal() as db, output_path.open("w", encoding="utf-8") as handle:
            filings = db.execute(
                select(Filing)
                .options(selectinload(Filing.analysis), selectinload(Filing.chunks))
                .where(Filing.raw_document_text.is_not(None))
                .order_by(Filing.published_at.desc())
            ).scalars()

            count = 0
            for filing in filings:
                record = {
                    "filing_id": filing.id,
                    "ticker": filing.ticker,
                    "company_name": filing.company_name,
                    "form_type": filing.form_type,
                    "tier": filing.tier,
                    "sec_items": filing.sec_items,
                    "published_at": filing.published_at.isoformat(),
                    "entry_link": filing.entry_link,
                    "raw_document_text": filing.raw_document_text,
                    "analysis": {
                        "impact": filing.analysis.impact_label if filing.analysis else None,
                        "summary": filing.analysis.summary_text if filing.analysis else None,
                        "key_takeaways": filing.analysis.key_takeaways if filing.analysis else [],
                    },
                    "chunks": [
                        {
                            "index": chunk.chunk_index,
                            "content": chunk.content,
                            "char_count": chunk.char_count,
                            "token_estimate": chunk.token_estimate,
                        }
                        for chunk in filing.chunks
                    ],
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

        return count

    def sync_telegram_once(self) -> dict:
        self._refresh_runtime_services()
        return self.telegram_assistant.sync_updates_once()

    def process_telegram_update(self, update: dict) -> dict:
        self._refresh_runtime_services()
        return self.telegram_assistant.process_update_payload(update)

    def sync_llm_usage_once(self, *, provider: str | None = None) -> dict:
        self._refresh_runtime_services()
        return self.llm_usage_service.sync_provider_usage_once(provider)

    def sync_prices_once(self, *, tickers: tuple[str, ...] = ()) -> dict:
        init_db()
        watchlist = list(dict.fromkeys([ticker.upper() for ticker in (tickers or tuple(WATCHLIST))]))
        if not self.price_service.available:
            raise RuntimeError(self.price_service.unavailable_reason)

        with SessionLocal() as db:
            run = PriceIngestionRun(provider=self.price_service.provider_name, status="running", requested_tickers=len(watchlist))
            db.add(run)
            db.commit()
            db.refresh(run)

            try:
                quotes = self.price_service.fetch_quotes(watchlist)
                quotes_by_ticker = {quote.ticker: quote for quote in quotes}
                stored = 0
                for ticker in watchlist:
                    quote = quotes_by_ticker.get(ticker)
                    if quote is None:
                        continue
                    db.add(
                        PriceSnapshot(
                            ticker=quote.ticker,
                            provider=quote.provider,
                            currency=quote.currency,
                            exchange=quote.exchange,
                            market_state=quote.market_state,
                            regular_market_price=quote.regular_market_price,
                            previous_close=quote.previous_close,
                            change_amount=quote.change_amount,
                            change_percent=quote.change_percent,
                            day_low=quote.day_low,
                            day_high=quote.day_high,
                            volume=quote.volume,
                            market_cap=quote.market_cap,
                            source_payload=quote.raw_payload,
                        )
                    )
                    stored += 1

                run.status = "completed"
                run.successful_tickers = stored
                run.failed_tickers = max(0, len(watchlist) - stored)
                run.completed_at = utc_now()
                db.commit()
                return {
                    "provider": self.price_service.provider_name,
                    "requested_tickers": len(watchlist),
                    "stored_quotes": stored,
                    "failed_tickers": run.failed_tickers,
                }
            except Exception as exc:
                logger.exception("Price sync failed")
                run.status = "failed"
                run.error_message = str(exc)
                run.failed_tickers = len(watchlist)
                run.completed_at = utc_now()
                db.commit()
                raise

    def latest_prices_snapshot(self, *, limit: int = 50, tickers: tuple[str, ...] = ()) -> dict:
        init_db()
        ticker_filter = {ticker.upper() for ticker in tickers}
        with SessionLocal() as db:
            snapshots = db.execute(
                select(PriceSnapshot)
                .order_by(PriceSnapshot.fetched_at.desc())
                .limit(max(limit * 10, limit))
            ).scalars().all()

            latest_by_ticker: dict[str, PriceSnapshot] = {}
            for snapshot in snapshots:
                if ticker_filter and snapshot.ticker not in ticker_filter:
                    continue
                if snapshot.ticker in latest_by_ticker:
                    continue
                latest_by_ticker[snapshot.ticker] = snapshot
                if len(latest_by_ticker) >= limit:
                    break

            return {
                "prices": [serialize_price_snapshot(snapshot) for snapshot in latest_by_ticker.values()],
            }

    def embed_chunks_once(self, *, limit: int = 32) -> dict:
        self._refresh_runtime_services()
        if not self.embedding_service.available:
            raise RuntimeError(self.embedding_service.unavailable_reason)

        init_db()
        processed = 0
        failed = 0

        with SessionLocal() as db:
            chunks = db.execute(
                select(FilingChunk)
                .order_by(FilingChunk.created_at.asc())
                .limit(max(limit * 4, limit))
            ).scalars().all()

            for chunk in chunks:
                content_hash = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
                existing = db.execute(
                    select(FilingChunkEmbedding).where(
                        FilingChunkEmbedding.chunk_id == chunk.id,
                        FilingChunkEmbedding.provider == self.embedding_service.provider_name,
                        FilingChunkEmbedding.model == self.embedding_service.selected_model,
                    )
                ).scalar_one_or_none()
                if existing and existing.status == "completed" and existing.content_hash == content_hash:
                    continue

                target = existing or FilingChunkEmbedding(
                    chunk_id=chunk.id,
                    provider=self.embedding_service.provider_name,
                    model=self.embedding_service.selected_model,
                    content_hash=content_hash,
                )
                if existing is None:
                    db.add(target)
                target.status = "running"
                target.content_hash = content_hash
                target.error_message = None
                db.flush()

                try:
                    payload = self.embedding_service.embed_text(chunk.content)
                    target.status = "completed"
                    target.vector = payload.vector
                    target.dimensions = payload.dimensions
                    target.raw_payload = payload.raw_payload
                    processed += 1
                except Exception as exc:
                    target.status = "failed"
                    target.error_message = str(exc)
                    failed += 1

                db.flush()
                if processed + failed >= limit:
                    break

            db.commit()
            return {
                "provider": self.embedding_service.provider_name,
                "model": self.embedding_service.selected_model,
                "processed": processed,
                "failed": failed,
            }

    def seed_label_tasks(self, *, limit: int, principal: AuthPrincipal | None = None) -> dict:
        return self.labeling_service.seed_tasks(limit=limit, principal=principal)

    def list_label_tasks(self, *, status: str = "open", limit: int = 50) -> dict:
        return self.labeling_service.list_tasks(status=status, limit=limit)

    def submit_label(self, *, task_id: int, payload: dict, principal: AuthPrincipal) -> dict:
        return self.labeling_service.submit_label(principal, task_id=task_id, payload=payload)

    def run_evaluation(self, *, limit: int, principal: AuthPrincipal | None = None) -> dict:
        self._refresh_runtime_services()
        return self.evaluation_service.run_evaluation(limit=limit, principal=principal)

    def list_evaluation_runs(self, *, limit: int = 20) -> dict:
        return self.evaluation_service.list_runs(limit=limit)

    def latest_evaluation_run(self) -> dict | None:
        return self.evaluation_service.latest_run()

    def dashboard_snapshot(self) -> dict:
        self._refresh_runtime_services()
        init_db()
        with SessionLocal() as db:
            filings = db.execute(
                select(Filing)
                .options(selectinload(Filing.analysis), selectinload(Filing.chunks))
                .order_by(Filing.published_at.desc())
                .limit(self.settings.dashboard_recent_limit)
            ).scalars().all()

            all_filings = db.execute(
                select(Filing)
                .options(selectinload(Filing.analysis))
                .order_by(Filing.published_at.desc())
            ).scalars().all()
            runs = db.execute(select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(10)).scalars().all()
            price_runs = db.execute(
                select(PriceIngestionRun).order_by(PriceIngestionRun.started_at.desc()).limit(5)
            ).scalars().all()
            chunk_count = db.scalar(select(func.count(FilingChunk.id))) or 0
            embedding_count = db.scalar(select(func.count(FilingChunkEmbedding.id))) or 0
            analyzed_count = db.scalar(select(func.count(AnalysisResult.id))) or 0
            label_task_open = db.scalar(select(func.count(LabelTask.id)).where(LabelTask.status == "open")) or 0
            latest_eval = db.execute(
                select(EvaluationRun).order_by(EvaluationRun.started_at.desc()).limit(1)
            ).scalar_one_or_none()
            telegram_subscribers = db.scalar(
                select(func.count(TelegramChat.id)).where(TelegramChat.alerts_enabled.is_(True))
            ) or 0
            telegram_assistant_chats = db.scalar(
                select(func.count(TelegramChat.id)).where(TelegramChat.assistant_enabled.is_(True))
            ) or 0

            sentiment_counts = {"利好": 0, "利空": 0, "中性": 0}
            for filing in all_filings:
                if filing.analysis and filing.analysis.impact_label in sentiment_counts:
                    sentiment_counts[filing.analysis.impact_label] += 1

            daily_counts: dict[str, dict[str, int]] = {}
            for filing in all_filings:
                day_key = filing.published_at.date().isoformat()
                day_bucket = daily_counts.setdefault(day_key, {"tier1": 0, "tier2": 0, "tier3": 0})
                day_bucket[f"tier{filing.tier}"] += 1

            tier_counts = {
                "tier1": len([filing for filing in all_filings if filing.tier == 1]),
                "tier2": len([filing for filing in all_filings if filing.tier == 2]),
                "tier3": len([filing for filing in all_filings if filing.tier == 3]),
            }
            filtered_feed = filter_feed_filings(
                filings,
                limit=self.settings.dashboard_recent_limit,
            )

            return {
                "app": self.settings.app_name,
                "environment": self.settings.app_env,
                "analysis": {
                    "provider": self.analysis_service.provider_name,
                    "model": self.analysis_service.selected_model,
                    "available": self.analysis_service.available,
                    "runtime_source": self.analysis_runtime_source,
                    "runtime_error": self.analysis_runtime_error,
                },
                "telegram": {
                    "bot_available": self.telegram.available,
                    "assistant_available": self.telegram_assistant.available,
                    "subscribed_chats": telegram_subscribers,
                    "assistant_chats": telegram_assistant_chats,
                    "webhook_enabled": bool(self.settings.telegram_webhook_secret),
                },
                "data_policy": {
                    "synthetic_data": False,
                    "demo_seed_data": False,
                    "primary_sources": [
                        "SEC EDGAR Atom feed",
                        "SEC filing HTML documents",
                    ],
                    "storage": "Only persisted database records are returned by the API.",
                },
                "watchlist_size": len(WATCHLIST),
                "totals": {
                    "filings": len(all_filings),
                    "analyzed": analyzed_count,
                    "chunks": chunk_count,
                    "embeddings": embedding_count,
                    "raw_text_ready": len([filing for filing in all_filings if filing.raw_document_text]),
                },
                "prices": {
                    "provider": self.price_service.provider_name,
                    "available": self.price_service.available,
                    "latest_quotes": self.latest_prices_snapshot(limit=10)["prices"],
                    "recent_runs": [serialize_price_run(run) for run in price_runs],
                },
                "llm_usage": self.llm_usage_service.summary_payload(),
                "labels": {
                    "open_tasks": label_task_open,
                },
                "evaluation": serialize_evaluation_run(latest_eval) if latest_eval else None,
                "tier_counts": tier_counts,
                "sentiment_counts": sentiment_counts,
                "daily_counts": [
                    {"date": day, **counts}
                    for day, counts in sorted(daily_counts.items())[-14:]
                ],
                "sec_brief": build_sec_brief(all_filings[:40]),
                "signal_index": build_signal_index(all_filings),
                "theme_clusters": build_theme_clusters(all_filings),
                "feed_facets": build_feed_facets(all_filings),
                "recent_filings": [serialize_filing_summary(filing) for filing in filtered_feed],
                "last_run": serialize_run(runs[0]) if runs else None,
                "recent_runs": [serialize_run(run) for run in runs],
            }

    def _refresh_runtime_services(self) -> None:
        try:
            runtime_config = self.gui_settings.load_runtime_config()
            self.effective_settings = self.gui_settings.apply_runtime_settings(self.settings, runtime_config)
            self.analysis_runtime_source = "gui" if runtime_config else "env"
            self.analysis_runtime_error = None
        except Exception as exc:
            logger.warning("Falling back to env-based analysis settings because GUI runtime config failed to load: %s", exc)
            self.effective_settings = self.settings
            self.analysis_runtime_source = "env"
            self.analysis_runtime_error = str(exc)

        self.analysis_service = build_analysis_service(self.effective_settings)
        self.embedding_service = build_embedding_service(self.effective_settings)
        self.llm_usage_service = LlmUsageService(self.settings, self.gui_settings.load_runtime_config)
        self.telegram = TelegramNotifier(self.settings)
        self.telegram_assistant = TelegramAssistantService(
            self.settings,
            analysis_service=self.analysis_service,
            usage_service=self.llm_usage_service,
        )
        self.evaluation_service = EvaluationService(self.effective_settings, self.analysis_service, self.llm_usage_service)

    def feed_snapshot(
        self,
        *,
        query: str = "",
        tier: str = "all",
        ticker: str = "",
        form_type: str = "",
        impact: str = "all",
        theme: str = "all",
        tickers: tuple[str, ...] = (),
        limit: int = 80,
    ) -> dict:
        init_db()
        with SessionLocal() as db:
            filings = db.execute(
                select(Filing)
                .options(selectinload(Filing.analysis), selectinload(Filing.chunks))
                .order_by(Filing.published_at.desc())
            ).scalars().all()
            filtered_all = filter_feed_filings(
                filings,
                query=query,
                tier=tier,
                ticker=ticker,
                form_type=form_type,
                impact=impact,
                theme=theme,
                tickers=tickers,
                limit=max(limit, len(filings) or 1),
            )
            filtered = filtered_all[:limit]
            return {
                "filters": {
                    "query": query,
                    "tier": tier,
                    "ticker": ticker,
                    "form_type": form_type,
                    "impact": impact,
                    "theme": theme,
                    "tickers": list(tickers),
                    "limit": limit,
                },
                "total_matches": len(filtered_all),
                "results": [serialize_filing_summary(filing) for filing in filtered],
                "facets": build_feed_facets(filings),
            }

    def filing_detail(self, filing_id: int) -> dict | None:
        init_db()
        with SessionLocal() as db:
            filing = db.execute(
                select(Filing)
                .options(selectinload(Filing.analysis), selectinload(Filing.chunks))
                .where(Filing.id == filing_id)
            ).scalar_one_or_none()

            if filing is None:
                return None

            payload = serialize_filing_summary(filing)
            payload["raw_document_text"] = filing.raw_document_text
            payload["chunks"] = [
                {
                    "index": chunk.chunk_index,
                    "content": chunk.content,
                    "char_count": chunk.char_count,
                    "token_estimate": chunk.token_estimate,
                }
                for chunk in filing.chunks
            ]
            return payload

    def _upsert_filing(self, db: Session, candidate: FilingCandidate) -> bool:
        filing = db.execute(select(Filing).where(Filing.entry_link == candidate.entry_link)).scalar_one_or_none()
        if filing is None:
            filing = Filing(
                accession_number=candidate.accession_number,
                ticker=candidate.ticker,
                company_name=candidate.company_name,
                title=candidate.title,
                form_type=candidate.form_type,
                tier=candidate.tier,
                sec_items=candidate.sec_items,
                raw_feed_summary=candidate.summary,
                entry_link=candidate.entry_link,
                published_at=candidate.published_at,
                analysis_status="pending",
                telegram_status="pending",
            )
            db.add(filing)
            db.commit()
            return True

        filing.accession_number = candidate.accession_number
        filing.title = candidate.title
        filing.form_type = candidate.form_type
        filing.tier = candidate.tier
        filing.sec_items = candidate.sec_items
        filing.raw_feed_summary = candidate.summary
        filing.company_name = candidate.company_name
        filing.ticker = candidate.ticker
        filing.published_at = candidate.published_at
        filing.last_seen_at = utc_now()
        db.commit()
        return False

    def _hydrate_document_and_chunks(self, db: Session, filing: Filing) -> None:
        document = self.sec_client.fetch_primary_document(filing.entry_link)
        if not document.text:
            return

        filing.primary_document_url = document.url
        filing.raw_document_text = document.text
        filing.document_char_count = len(document.text)

        existing_chunks = db.execute(select(FilingChunk).where(FilingChunk.filing_id == filing.id)).scalars().all()
        for chunk in existing_chunks:
            db.delete(chunk)
        db.flush()

        for index, chunk in enumerate(
            chunk_text(
                document.text,
                chunk_size=self.settings.llm_chunk_size,
                overlap=self.settings.llm_chunk_overlap,
            )
        ):
            db.add(
                FilingChunk(
                    filing_id=filing.id,
                    chunk_index=index,
                    content=chunk,
                    char_count=len(chunk),
                    token_estimate=estimate_tokens(chunk),
                    chunk_metadata={"source": "sec", "ticker": filing.ticker, "form_type": filing.form_type},
                )
            )

        db.flush()

    def _run_analysis(self, db: Session, filing: Filing) -> bool:
        try:
            payload = self.analysis_service.analyze(
                ticker=filing.ticker,
                company_name=filing.company_name,
                form_type=filing.form_type,
                document_text=filing.raw_document_text or "",
            )
        except Exception as exc:
            filing.analysis_status = "failed"
            filing.analysis_error = str(exc)
            db.flush()
            return False

        filing.analysis_status = "completed"
        filing.analysis_error = None
        db.add(
            AnalysisResult(
                filing_id=filing.id,
                provider=payload.provider,
                model=payload.model,
                impact_label=payload.impact_label,
                summary_text=payload.summary_text,
                key_takeaways=payload.key_takeaways,
                raw_payload=payload.raw_payload,
            )
        )
        self.llm_usage_service.record_usage(
            db,
            provider=payload.provider,
            model=payload.model,
            feature="analysis",
            usage=payload.usage,
            raw_payload=payload.raw_payload,
            metadata={"filing_id": filing.id},
        )
        db.flush()
        return True

    def _send_telegram(self, db: Session, filing: Filing) -> None:
        try:
            deliveries = self.telegram.send_filing_alert(db, filing, filing.analysis)
        except Exception as exc:
            filing.telegram_status = f"failed:{exc}"
            db.flush()
            return

        filing.telegram_status = f"sent:{deliveries}"
        filing.telegram_sent_at = utc_now()
        db.flush()


def serialize_filing_summary(filing: Filing) -> dict:
    themes = classify_filing_themes(filing)
    return {
        "id": filing.id,
        "ticker": filing.ticker,
        "company_name": filing.company_name,
        "title": filing.title,
        "form_type": filing.form_type,
        "tier": filing.tier,
        "sec_items": filing.sec_items,
        "raw_feed_summary": clean_text(filing.raw_feed_summary),
        "entry_link": filing.entry_link,
        "primary_document_url": filing.primary_document_url,
        "published_at": filing.published_at.isoformat(),
        "ingested_at": filing.ingested_at.isoformat(),
        "analysis_status": filing.analysis_status,
        "analysis_error": filing.analysis_error,
        "telegram_status": filing.telegram_status,
        "document_char_count": filing.document_char_count,
        "chunk_count": len(filing.chunks),
        "signal_score": compute_filing_signal_score(filing),
        "themes": themes,
        "analysis": (
            {
                "provider": filing.analysis.provider,
                "model": filing.analysis.model,
                "impact": filing.analysis.impact_label,
                "summary": filing.analysis.summary_text,
                "key_takeaways": filing.analysis.key_takeaways,
            }
            if filing.analysis
            else None
        ),
    }


def serialize_run(run: IngestionRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "entries_seen": run.entries_seen,
        "matched_entries": run.matched_entries,
        "new_filings": run.new_filings,
        "updated_filings": run.updated_filings,
        "analyzed_filings": run.analyzed_filings,
        "error_message": run.error_message,
    }


def serialize_price_snapshot(snapshot: PriceSnapshot) -> dict:
    return {
        "id": snapshot.id,
        "ticker": snapshot.ticker,
        "provider": snapshot.provider,
        "currency": snapshot.currency,
        "exchange": snapshot.exchange,
        "market_state": snapshot.market_state,
        "regular_market_price": snapshot.regular_market_price,
        "previous_close": snapshot.previous_close,
        "change_amount": snapshot.change_amount,
        "change_percent": snapshot.change_percent,
        "day_low": snapshot.day_low,
        "day_high": snapshot.day_high,
        "volume": snapshot.volume,
        "market_cap": snapshot.market_cap,
        "fetched_at": snapshot.fetched_at.isoformat(),
    }


def serialize_price_run(run: PriceIngestionRun) -> dict:
    return {
        "id": run.id,
        "provider": run.provider,
        "status": run.status,
        "requested_tickers": run.requested_tickers,
        "successful_tickers": run.successful_tickers,
        "failed_tickers": run.failed_tickers,
        "error_message": run.error_message,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def serialize_evaluation_run(run: EvaluationRun) -> dict:
    return {
        "id": run.id,
        "provider": run.provider,
        "model": run.model,
        "label_type": run.label_type,
        "status": run.status,
        "total_examples": run.total_examples,
        "completed_examples": run.completed_examples,
        "impact_accuracy": run.impact_accuracy,
        "summary_similarity_mean": run.summary_similarity_mean,
        "error_message": run.error_message,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }
