from __future__ import annotations

from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import Settings
from ..database import SessionLocal, init_db
from ..models import EvaluationExample, EvaluationRun, Filing, LabelRecord, utc_now
from .analysis import BaseAnalysisService
from .auth import AuthPrincipal, role_allows

if TYPE_CHECKING:
    from .llm_usage import LlmUsageService


class EvaluationService:
    def __init__(
        self,
        settings: Settings,
        analysis_service: BaseAnalysisService,
        usage_service: LlmUsageService | None = None,
    ) -> None:
        self.settings = settings
        self.analysis_service = analysis_service
        self.usage_service = usage_service

    def run_evaluation(self, *, limit: int, principal: AuthPrincipal | None = None) -> dict:
        if principal is not None and not role_allows(principal.role, "operator"):
            raise RuntimeError("Operator or admin role is required to run evaluations.")
        if not self.analysis_service.available:
            raise RuntimeError(self.analysis_service.unavailable_reason)

        init_db()
        with SessionLocal() as db:
            labels = db.execute(
                select(LabelRecord)
                .options(selectinload(LabelRecord.filing))
                .where(LabelRecord.label_type == "filing_assessment", LabelRecord.filing_id.is_not(None))
                .order_by(LabelRecord.created_at.desc())
                .limit(limit)
            ).scalars().all()

            run = EvaluationRun(
                provider=self.analysis_service.provider_name,
                model=self.analysis_service.selected_model,
                label_type="filing_assessment",
                status="running",
                total_examples=len(labels),
                created_by_user_id=principal.user_id if principal else None,
            )
            db.add(run)
            db.flush()

            similarities: list[float] = []
            impact_matches = 0
            impact_total = 0

            try:
                for label in labels:
                    filing = label.filing
                    if filing is None or not filing.raw_document_text:
                        continue

                    payload = self.analysis_service.analyze(
                        ticker=filing.ticker,
                        company_name=filing.company_name,
                        form_type=filing.form_type,
                        document_text=filing.raw_document_text,
                    )
                    if self.usage_service is not None:
                        self.usage_service.record_usage(
                            db,
                            provider=payload.provider,
                            model=payload.model,
                            feature="evaluation",
                            usage=payload.usage,
                            raw_payload=payload.raw_payload,
                            metadata={"evaluation_run_id": run.id, "filing_id": filing.id},
                        )
                    summary_similarity = None
                    if label.summary_text:
                        summary_similarity = _text_similarity(label.summary_text, payload.summary_text)
                        similarities.append(summary_similarity)

                    impact_match = None
                    if label.impact_label:
                        impact_total += 1
                        impact_match = label.impact_label == payload.impact_label
                        if impact_match:
                            impact_matches += 1

                    db.add(
                        EvaluationExample(
                            evaluation_run_id=run.id,
                            filing_id=filing.id,
                            label_record_id=label.id,
                            predicted_impact=payload.impact_label,
                            predicted_summary=payload.summary_text,
                            predicted_takeaways=payload.key_takeaways,
                            impact_match=impact_match,
                            summary_similarity=summary_similarity,
                            raw_payload=payload.raw_payload,
                        )
                    )
                    run.completed_examples += 1
                    db.flush()

                run.status = "completed"
                run.completed_at = utc_now()
                run.impact_accuracy = (impact_matches / impact_total) if impact_total else None
                run.summary_similarity_mean = (sum(similarities) / len(similarities)) if similarities else None
                db.commit()
            except Exception as exc:
                run.status = "failed"
                run.error_message = str(exc)
                run.completed_at = utc_now()
                db.commit()
                raise

            db.refresh(run)
            return {"evaluation_run": self._serialize_run(run)}

    def list_runs(self, *, limit: int = 20) -> dict:
        init_db()
        with SessionLocal() as db:
            runs = db.execute(
                select(EvaluationRun)
                .options(selectinload(EvaluationRun.examples))
                .order_by(EvaluationRun.started_at.desc())
                .limit(limit)
            ).scalars().all()
            return {"runs": [self._serialize_run(run) for run in runs]}

    def latest_run(self) -> dict | None:
        init_db()
        with SessionLocal() as db:
            run = db.execute(
                select(EvaluationRun)
                .options(selectinload(EvaluationRun.examples))
                .order_by(EvaluationRun.started_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if run is None:
                return None
            return {"evaluation_run": self._serialize_run(run)}

    def _serialize_run(self, run: EvaluationRun) -> dict:
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
            "examples_count": len(run.examples),
        }


def _text_similarity(left: str, right: str) -> float:
    left_text = left.strip()
    right_text = right.strip()
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio()
