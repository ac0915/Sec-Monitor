from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import Settings
from ..database import SessionLocal, init_db
from ..models import Filing, LabelRecord, LabelTask, utc_now
from .auth import AuthPrincipal, role_allows


class LabelingService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def seed_tasks(self, *, limit: int, principal: AuthPrincipal | None = None) -> dict:
        init_db()
        with SessionLocal() as db:
            existing_filing_ids = {
                filing_id
                for filing_id in db.execute(
                    select(LabelTask.filing_id).where(LabelTask.filing_id.is_not(None), LabelTask.task_type == "filing_assessment")
                ).scalars()
                if filing_id is not None
            }
            filings = db.execute(
                select(Filing)
                .options(selectinload(Filing.analysis))
                .where(Filing.raw_document_text.is_not(None))
                .order_by(Filing.published_at.desc())
                .limit(max(limit * 3, limit))
            ).scalars().all()

            created = 0
            tasks: list[dict] = []
            for filing in filings:
                if filing.id in existing_filing_ids:
                    continue
                task = LabelTask(
                    filing_id=filing.id,
                    analysis_result_id=filing.analysis.id if filing.analysis else None,
                    task_type="filing_assessment",
                    status="open",
                    priority=max(1, self.settings.label_default_priority - (filing.tier * 5)),
                    task_metadata={
                        "ticker": filing.ticker,
                        "company_name": filing.company_name,
                        "form_type": filing.form_type,
                        "tier": filing.tier,
                        "entry_link": filing.entry_link,
                    },
                    created_by_user_id=principal.user_id if principal else None,
                )
                db.add(task)
                db.flush()
                created += 1
                tasks.append(self._serialize_task(task))
                if created >= limit:
                    break

            db.commit()
            return {"created": created, "tasks": tasks}

    def list_tasks(self, *, status: str = "open", limit: int = 50) -> dict:
        init_db()
        with SessionLocal() as db:
            query = (
                select(LabelTask)
                .options(selectinload(LabelTask.filing), selectinload(LabelTask.labels))
                .order_by(LabelTask.priority.asc(), LabelTask.created_at.desc())
                .limit(limit)
            )
            if status != "all":
                query = query.where(LabelTask.status == status)
            tasks = db.execute(query).scalars().all()
            return {"tasks": [self._serialize_task(task) for task in tasks]}

    def submit_label(self, principal: AuthPrincipal, *, task_id: int, payload: dict) -> dict:
        if not role_allows(principal.role, "operator"):
            raise RuntimeError("Operator or admin role is required to submit labels.")

        init_db()
        with SessionLocal() as db:
            task = db.execute(
                select(LabelTask)
                .options(selectinload(LabelTask.filing), selectinload(LabelTask.labels))
                .where(LabelTask.id == task_id)
            ).scalar_one_or_none()
            if task is None:
                raise RuntimeError("Label task not found.")

            label = LabelRecord(
                task_id=task.id,
                filing_id=task.filing_id,
                analysis_result_id=task.analysis_result_id,
                chunk_id=task.chunk_id,
                labeler_user_id=principal.user_id,
                label_type=task.task_type,
                impact_label=_opt_str(payload.get("impact_label")),
                relevance_label=_opt_str(payload.get("relevance_label")),
                summary_text=_opt_str(payload.get("summary_text")),
                notes=_opt_str(payload.get("notes")),
                label_payload=payload.get("label_payload", {}) if isinstance(payload.get("label_payload"), dict) else {},
            )
            db.add(label)
            task.status = "completed"
            task.assigned_to_user_id = principal.user_id
            task.completed_at = utc_now()
            db.flush()
            db.commit()
            db.refresh(task)
            return {"task": self._serialize_task(task), "label_id": label.id}

    def _serialize_task(self, task: LabelTask) -> dict:
        filing = task.filing
        return {
            "id": task.id,
            "task_type": task.task_type,
            "status": task.status,
            "priority": task.priority,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "task_metadata": task.task_metadata,
            "labels_count": len(task.labels),
            "filing": (
                {
                    "id": filing.id,
                    "ticker": filing.ticker,
                    "company_name": filing.company_name,
                    "form_type": filing.form_type,
                    "tier": filing.tier,
                    "published_at": filing.published_at.isoformat(),
                }
                if filing
                else None
            ),
        }


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
