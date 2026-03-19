from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .database import SessionLocal, init_db
from .models import AnalysisResult, Filing, FilingChunk, IngestionRun, TelegramChat, utc_now
from .services.analysis import build_analysis_service
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
        self.sec_client = SECClient(self.settings)
        self.analysis_service = build_analysis_service(self.settings)
        self.telegram = TelegramNotifier(self.settings)
        self.telegram_assistant = TelegramAssistantService(self.settings, analysis_service=self.analysis_service)

    def ingest_once(self) -> dict:
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
        return self.telegram_assistant.sync_updates_once()

    def process_telegram_update(self, update: dict) -> dict:
        return self.telegram_assistant.process_update_payload(update)

    def dashboard_snapshot(self) -> dict:
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
            chunk_count = db.scalar(select(func.count(FilingChunk.id))) or 0
            analyzed_count = db.scalar(select(func.count(AnalysisResult.id))) or 0
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
                    "raw_text_ready": len([filing for filing in all_filings if filing.raw_document_text]),
                },
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
