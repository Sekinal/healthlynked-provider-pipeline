"""Persistence + audit lineage. Every decision is written so it can be traced
back to sources, weights, and the confidence formula inputs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import select

from db.models import (
    CostLedger,
    Provider,
    ProviderVersion,
    ProposedChange,
    ReviewItem,
    Run,
    SourceSnapshot,
)
from db.session import get_session
from pipeline.schemas import Action, ProviderRecord, Recommendation


def start_run(notes: str | None = None) -> int:
    with get_session() as s:
        run = Run(notes=notes)
        s.add(run)
        s.commit()
        s.refresh(run)
        return run.id


def finish_run(run_id: int, processed: int, no_change: int, auto: int, review: int) -> None:
    with get_session() as s:
        run = s.get(Run, run_id)
        if run:
            run.finished_at = datetime.now(timezone.utc)
            run.records_processed = processed
            run.no_change = no_change
            run.auto_update = auto
            run.human_review = review
            s.add(run)
            s.commit()


def save_snapshot(npi: str, source_type: str, content_hash: str,
                  payload: dict, url: str | None = None) -> int:
    with get_session() as s:
        snap = SourceSnapshot(
            npi=npi, source_type=source_type, content_hash=content_hash,
            payload=payload, url=url,
        )
        s.add(snap)
        s.commit()
        s.refresh(snap)
        return snap.id


def log_cost(run_id: Optional[int], npi: Optional[str], category: str,
             quantity: float, usd: float, detail: str | None = None) -> None:
    with get_session() as s:
        s.add(CostLedger(run_id=run_id, npi=npi, category=category,
                         detail=detail, quantity=quantity, usd=usd))
        s.commit()


def _upsert_provider(s, record: ProviderRecord) -> Provider:
    prov = s.get(Provider, record.npi)
    if prov is None:
        prov = Provider(npi=record.npi, provider_id=record.provider_id)
    for f in ("provider_id", "provider_name", "specialty", "practice_name",
              "address", "phone", "website", "active_status"):
        setattr(prov, f, getattr(record, f))
    prov.last_verified_date = str(record.last_verified_date) if record.last_verified_date else None
    s.add(prov)
    return prov


def persist_recommendation(record: ProviderRecord, rec: Recommendation,
                           run_id: int | None) -> None:
    """Write proposed changes + route: auto_update applies + versions the record;
    human_review enqueues; no_change just records the audit row."""
    with get_session() as s:
        prov = _upsert_provider(s, record)

        for ch in rec.changes:
            s.add(ProposedChange(
                npi=rec.npi, run_id=run_id, field=ch.field,
                old_value=ch.old_value, new_value=ch.new_value,
                field_confidence=ch.confidence_score,
                overall_confidence=rec.overall_confidence,
                action=rec.recommended_action.value, reason=rec.reason,
                supporting_sources=ch.supporting_sources,
                conflicting_sources=ch.conflicting_sources,
                llm_tier_used=rec.llm_tier_used,
            ))

        if rec.recommended_action == Action.auto_update:
            # apply changes, write a new SCD-2 version
            for ch in rec.changes:
                setattr(prov, ch.field, ch.new_value)
            prov.updated_at = datetime.now(timezone.utc)
            s.add(prov)
            n_versions = len(s.exec(
                select(ProviderVersion).where(ProviderVersion.npi == rec.npi)
            ).all())
            s.add(ProviderVersion(
                npi=rec.npi, version=n_versions + 1,
                snapshot=prov.model_dump(mode="json"),
                changed_fields=[c.field for c in rec.changes],
                run_id=run_id,
            ))
        elif rec.recommended_action == Action.human_review:
            s.add(ReviewItem(
                npi=rec.npi, run_id=run_id,
                recommendation=rec.model_dump(mode="json"),
            ))

        s.commit()
