"""SQLModel ORM tables = the system of record + full audit lineage.

Works on SQLite (local dev) and Postgres (docker compose) via DATABASE_URL.
JSON columns hold structured snapshots/evidence so every decision is traceable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Provider(SQLModel, table=True):
    """Current canonical record, one row per NPI."""

    __tablename__ = "providers"

    npi: str = Field(primary_key=True)
    provider_id: str = Field(index=True)
    provider_name: Optional[str] = None
    specialty: Optional[str] = None
    practice_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    active_status: Optional[str] = None
    last_verified_date: Optional[str] = None
    updated_at: datetime = Field(default_factory=_utcnow)


class ProviderVersion(SQLModel, table=True):
    """Append-only history (SCD-2). Each accepted change writes a new version
    -> powers provider-movement detection and change history."""

    __tablename__ = "provider_versions"

    id: Optional[int] = Field(default=None, primary_key=True)
    npi: str = Field(index=True)
    version: int
    snapshot: dict = Field(sa_column=Column(JSON))  # full record at this version
    changed_fields: list = Field(default_factory=list, sa_column=Column(JSON))
    run_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_utcnow)


class SourceSnapshot(SQLModel, table=True):
    """Raw payload from a source (API JSON / scraped HTML), content-hashed for
    reproducible audit. In prod the payload may be a MinIO pointer."""

    __tablename__ = "source_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)
    npi: str = Field(index=True)
    source_type: str
    url: Optional[str] = None
    content_hash: str = Field(index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    fetched_at: datetime = Field(default_factory=_utcnow)


class ProposedChange(SQLModel, table=True):
    """One field-level change proposal with its scoring + decision."""

    __tablename__ = "proposed_changes"

    id: Optional[int] = Field(default=None, primary_key=True)
    npi: str = Field(index=True)
    run_id: Optional[int] = Field(default=None, index=True)
    field: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    field_confidence: float = 0.0
    overall_confidence: float = 0.0
    action: str = "no_change"
    reason: str = ""
    supporting_sources: list = Field(default_factory=list, sa_column=Column(JSON))
    conflicting_sources: list = Field(default_factory=list, sa_column=Column(JSON))
    evidence: list = Field(default_factory=list, sa_column=Column(JSON))
    llm_tier_used: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class ReviewItem(SQLModel, table=True):
    """Human-review queue entry."""

    __tablename__ = "review_queue"

    id: Optional[int] = Field(default=None, primary_key=True)
    npi: str = Field(index=True)
    run_id: Optional[int] = Field(default=None, index=True)
    recommendation: dict = Field(sa_column=Column(JSON))
    status: str = Field(default="pending", index=True)  # pending|approved|rejected
    reviewer: Optional[str] = None
    notes: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)


class Run(SQLModel, table=True):
    """Pipeline run metadata + per-branch counts."""

    __tablename__ = "runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None
    records_processed: int = 0
    no_change: int = 0
    auto_update: int = 0
    human_review: int = 0
    notes: Optional[str] = None


class CostLedger(SQLModel, table=True):
    """Per-run spend accounting -> powers the cost-efficiency dashboard."""

    __tablename__ = "cost_ledger"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, index=True)
    npi: Optional[str] = Field(default=None, index=True)
    category: str  # "nppes_api" | "proxy_bytes" | "llm_tier1" | "llm_tier2"
    detail: Optional[str] = None
    quantity: float = 0.0  # tokens / bytes / calls
    usd: float = 0.0
    created_at: datetime = Field(default_factory=_utcnow)
