"""FastAPI service: exposes recommendations + the human-review workflow so
HealthLynked systems can integrate with the pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from db.models import CostLedger, Provider, ProviderVersion, ProposedChange, ReviewItem, Run
from db.session import get_session, init_db

app = FastAPI(title="HealthLynked Directory Update Pipeline", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/providers/{npi}")
def get_provider(npi: str) -> dict:
    with get_session() as s:
        prov = s.get(Provider, npi)
        if not prov:
            raise HTTPException(404, "provider not found")
        versions = s.exec(select(ProviderVersion).where(ProviderVersion.npi == npi)).all()
        return {"provider": prov.model_dump(mode="json"), "versions": len(versions)}


@app.get("/recommendations")
def recommendations(action: Optional[str] = None, limit: int = 50) -> list[dict]:
    with get_session() as s:
        q = select(ProposedChange).order_by(ProposedChange.created_at.desc()).limit(limit)
        if action:
            q = q.where(ProposedChange.action == action)
        return [c.model_dump(mode="json") for c in s.exec(q).all()]


@app.get("/review/pending")
def pending_reviews(limit: int = 50) -> list[dict]:
    with get_session() as s:
        q = (select(ReviewItem).where(ReviewItem.status == "pending")
             .order_by(ReviewItem.created_at).limit(limit))
        return [r.model_dump(mode="json") for r in s.exec(q).all()]


class ReviewDecision(BaseModel):
    decision: str  # "approved" | "rejected"
    reviewer: str
    notes: Optional[str] = None


@app.post("/review/{item_id}")
def resolve_review(item_id: int, body: ReviewDecision) -> dict:
    if body.decision not in {"approved", "rejected"}:
        raise HTTPException(400, "decision must be approved or rejected")
    with get_session() as s:
        item = s.get(ReviewItem, item_id)
        if not item:
            raise HTTPException(404, "review item not found")
        item.status = body.decision
        item.reviewer = body.reviewer
        item.notes = body.notes
        item.resolved_at = datetime.now(timezone.utc)
        s.add(item)

        # On approval, apply the changes + write a new SCD-2 version.
        if body.decision == "approved":
            rec = item.recommendation
            prov = s.get(Provider, item.npi)
            if prov:
                for ch in rec.get("changes", []):
                    setattr(prov, ch["field"], ch["new_value"])
                prov.updated_at = datetime.now(timezone.utc)
                s.add(prov)
                n = len(s.exec(select(ProviderVersion).where(ProviderVersion.npi == item.npi)).all())
                s.add(ProviderVersion(
                    npi=item.npi, version=n + 1, snapshot=prov.model_dump(mode="json"),
                    changed_fields=[c["field"] for c in rec.get("changes", [])],
                    run_id=item.run_id,
                ))
        s.commit()
        return {"status": item.status, "npi": item.npi}


@app.get("/cost/summary")
def cost_summary() -> dict:
    with get_session() as s:
        ledger = s.exec(select(CostLedger)).all()
        by_cat: dict[str, float] = {}
        for row in ledger:
            by_cat[row.category] = round(by_cat.get(row.category, 0.0) + row.usd, 6)
        runs = s.exec(select(Run)).all()
        total_records = sum(r.records_processed for r in runs) or 1
        total_usd = round(sum(by_cat.values()), 6)
        return {
            "by_category": by_cat,
            "total_usd": total_usd,
            "records_processed": sum(r.records_processed for r in runs),
            "usd_per_1k_records": round(total_usd / total_records * 1000, 4),
        }
