"""Pydantic models shared across the pipeline.

The output schema deliberately mirrors the HealthLynked competition spec so the
recommendation JSON is drop-in compatible with their example.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# MVP fields we track. Order matters for stable diffs / display.
TRACKED_FIELDS = [
    "provider_name",
    "specialty",
    "practice_name",
    "address",
    "phone",
    "website",
    "active_status",
]


class Action(str, Enum):
    no_change = "no_change"
    auto_update = "auto_update"
    human_review = "human_review"


class ProviderRecord(BaseModel):
    """A HealthLynked directory record (also the canonical post-update shape)."""

    provider_id: str
    npi: str
    provider_name: Optional[str] = None
    specialty: Optional[str] = None
    practice_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    active_status: Optional[str] = None  # "active" | "inactive"
    last_verified_date: Optional[date] = None


class Evidence(BaseModel):
    """One source's observation of a single field value."""

    field: str
    value: str  # normalized value
    raw_value: Optional[str] = None  # value as seen at the source
    source: str  # e.g. "NPI Registry"
    weight: float  # source reliability weight w_s
    recency: float = 1.0  # recency factor r_s in [0, 1]
    snapshot_id: Optional[str] = None  # FK into source_snapshots (audit)


class FieldChange(BaseModel):
    """A proposed change to one field, matching the competition output spec."""

    field: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    confidence_score: float
    supporting_sources: list[str] = Field(default_factory=list)
    conflicting_sources: list[str] = Field(default_factory=list)
    auto_eligible: bool = False


class Recommendation(BaseModel):
    """Top-level pipeline output for a single provider record."""

    provider_id: str
    npi: str
    change_detected: bool = False
    changes: list[FieldChange] = Field(default_factory=list)
    overall_confidence: float = 1.0
    recommended_action: Action = Action.no_change
    reason: str = ""
    # contested fields where the current value narrowly won but a competing
    # source has meaningful support -> surfaced for human attention.
    review_flags: list[str] = Field(default_factory=list)
    # provenance / cost metadata
    sources_consulted: list[str] = Field(default_factory=list)
    llm_tier_used: Optional[str] = None  # None | "tier1" | "tier2"
