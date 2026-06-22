"""Central configuration: thresholds, source weights, model tiers.

Tunables live here so the cost/accuracy trade-off is auditable in one place.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- secrets / infra ---
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    database_url: str = "sqlite:///./healthlynked.db"
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM tiers ---
    # Tier 1: cheap default that handles the bulk of conflict reconciliation.
    llm_tier1_model: str = "deepseek/deepseek-v4-flash"
    # Tier 2: frontier-class escalation, only for the low-confidence residual.
    llm_tier2_model: str = "z-ai/glm-5.2"

    # --- decision thresholds ---
    auto_update_threshold: float = 0.85   # field_conf >= this is auto-eligible
    human_review_floor: float = 0.60      # below this, never auto; flag conflict
    tier1_escalate_below: float = 0.75    # tier-1 self-confidence below -> escalate
    # Recency: r_s = floor + (1-floor)*exp(-age_days/tau). The floor keeps
    # old-but-authoritative data (e.g. NPPES timestamps years old) from zeroing out.
    recency_tau_days: float = 730.0
    recency_floor: float = 0.5

    # --- entity-resolution fuzzy band (rapidfuzz token_set_ratio / 100) ---
    fuzzy_low: float = 0.80   # below -> not a match
    fuzzy_high: float = 0.92  # above -> auto-match; between -> LLM tie-break

    # --- staleness ---
    stale_after_days: int = 180  # records older than this enter the funnel

    # A competing value backed by >= this much evidence makes a field "contested"
    # even when the current value narrowly wins -> route to human review.
    contest_threshold: float = 0.5


settings = Settings()


# Field-specific source reliability weights w_s in [0, 1].
# Default per source, with overrides for fields a source is especially good at.
SOURCE_WEIGHTS: dict[str, float] = {
    "State Medical Board": 0.95,
    "NPI Registry": 0.90,
    "CMS Doctors & Clinicians": 0.85,
    "Practice Website": 0.75,
    "Google Business": 0.60,
    "Third-Party Directory": 0.45,
}

# Source families for *independence*. Two sources in the same family are treated
# as correlated (e.g. Google Business often mirrors the practice website), so they
# do NOT count as two independent corroborations for the auto-update gate.
SOURCE_FAMILY: dict[str, str] = {
    "NPI Registry": "nppes",
    "CMS Doctors & Clinicians": "cms",
    "State Medical Board": "board",
    "Practice Website": "web",
    "Google Business": "web",
    "Third-Party Directory": "web",
}


def family_of(source: str) -> str:
    # Unknown sources collapse into one shared family so that two unmapped
    # (and likely correlated/unvetted) labels never count as independent
    # corroboration. Map new trusted sources explicitly to grant independence.
    return SOURCE_FAMILY.get(source, "unknown")

# (source, field) -> weight override
FIELD_WEIGHT_OVERRIDES: dict[tuple[str, str], float] = {
    ("NPI Registry", "active_status"): 0.95,
    ("State Medical Board", "active_status"): 0.97,
    ("Practice Website", "website"): 0.90,
    ("Practice Website", "phone"): 0.80,
}

# Sources strong enough to (alone) justify an auto-update when high-confidence.
AUTHORITATIVE_SOURCES: set[str] = {
    "NPI Registry",
    "State Medical Board",
    "CMS Doctors & Clinicians",
}

# Fields too risky to ever auto-update; always route to human review on change.
HIGH_RISK_FIELDS: set[str] = {"npi", "provider_name", "active_status"}

# The existing record is a possibly-stale baseline, NEVER corroborating evidence.
# Any evidence carrying these source labels is dropped before scoring.
BASELINE_SOURCES: set[str] = {"HealthLynked", "baseline", "existing_record"}


def weight_for(source: str, field: str) -> float:
    """Resolve the reliability weight for a (source, field) pair."""
    return FIELD_WEIGHT_OVERRIDES.get((source, field), SOURCE_WEIGHTS.get(source, 0.40))
