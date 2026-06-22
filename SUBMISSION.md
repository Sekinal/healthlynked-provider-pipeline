# HealthLynked Provider Directory Update Pipeline

**A repeatable, self-verifying AI pipeline that keeps provider and practice records accurate for under $2 per 1,000 records.**

![thumbnail](assets/thumbnail.png)

## The problem
Healthcare provider data goes stale constantly as doctors move, practices rebrand, merge, or close, phone numbers and suite numbers change, and providers join or leave groups. Sources also disagree with each other. Manual maintenance is slow, expensive, and impossible to scale, so the directory drifts out of date and patients reach the wrong place.

## Core idea: a cost funnel
The biggest cost lever is not which model you pick, it is how few records reach the expensive stages. The pipeline is a funnel where every stage filters, so each costlier stage touches only the residual the cheaper stages could not resolve.

1. **Staleness and risk scoring (free, deterministic).** Only records that look stale or risky enter the funnel each run, based on time since last verification and field volatility.
2. **Free authoritative sources (free).** The NPPES NPI Registry API (public, no key) plus CMS bulk files resolve name, NPI, specialty, practice, address, phone, and active status for most records at zero marginal cost. Responses are cached so re-runs cost nothing.
3. **Deterministic normalization (free).** Addresses (usaddress and scourgify), phones (to E.164), names, and specialties (NUCC taxonomy) are canonicalized before comparison, so cosmetic differences never trigger a change.
4. **Matching and duplicate detection (free, narrow LLM tie break).** Block by NPI, ZIP, or phone, then fuzzy name similarity. Only the ambiguous band escalates. Surfaces duplicates, moved providers, and practice merges.
5. **Web enrichment, only for gaps or conflicts.** Crawlee orchestrates a tiered fetch: HTTP-first with browser-identical TLS fingerprints (curl_cffi / httpcloak), then CloakBrowser stealth as a last resort. Website discovery uses name-token matching so it prefers finding nothing over scraping the wrong site.
6. **Confidence scoring and decision.** A source-weighted score decides each field; the record routes to no change, auto update, or human review.
7. **Audit and persistence.** Every decision and its evidence are written to Postgres with full lineage.

## Confidence scoring (explainable by construction)
For each field, evidence for a value combines with a noisy-OR over independent source **families**:

```
evidence(v) = 1 - PROD over families ( 1 - w_s * r_s )
field_confidence = evidence(winner) * (1 - aggregate dissent)
```

`w_s` is a field-specific source weight; `r_s` is a recency factor that decays with age but never to zero. Correlated web sources collapse into one family so they cannot fake independent corroboration. Two well-supported but disagreeing values deflate confidence toward zero and route to human review (the unsafe-update case). The existing record is a possibly-stale baseline, never corroboration.

Weights: State Board 0.95, NPI Registry 0.90 (0.95 status), CMS 0.85, Practice Website 0.75, Google Business 0.60, aggregators 0.45.

## Safe auto-update rules
A field auto-updates only if confidence >= 0.85 AND backed by two independent families or one authoritative government source, with no unresolved conflict. A record auto-updates only when all changed fields qualify. Always human-reviewed: changes to NPI, provider name, or active status; any single-weak-source change; any contested near-tie.

## LLM layer (sparing, cheap, private)
Fires only on genuine conflicts. A bake-off over five OpenRouter models picks the cheapest accurate one: **DeepSeek V4 Flash, 100% accuracy at ~$0.067 per 1,000 conflicts, self-hostable for privacy**. **GLM 5.2** (also self-hostable) is a second tier reserved for the low-confidence residual. The whole layer can run on-premises so no provider data leaves the network.

## Human review, audit, data model
Uncertain records land in a Streamlit dashboard (proposed change, sources, confidence, one-click approve that applies + versions the record). Postgres is the system of record: canonical table, append-only SCD-2 version history (provider-movement detection + change history), content-hashed source snapshots, review queue, runs, and a cost ledger. Alembic owns the schema.

## Cost per 1,000 records
Staleness + NPPES + normalization are free; web enrichment touches ~25% of records (cents of bandwidth); LLM touches only conflicts (cents). Total **< $2 / 1,000 records**; the dashboard reports live $/1k from the cost ledger.

## Measured accuracy (live, 40 real NPIs)
- False-positive rate: **0%** (never invents a change on a correct record)
- Detection rate: **100%** (catches every injected error)
- Value-correctness: **100%** (proposes the right registry value)

## Scalability, practicality, quality
Postgres + Redis + MinIO + FastAPI + APScheduler worker + Streamlit, all behind one Docker Compose file. The funnel keeps per-record cost flat as volume grows. The scoring/decision code was hardened over **three rounds of independent adversarial review** (codex), each finding fixed and locked with regression tests; 50+ deterministic tests pass with no network. Verified running in production via Docker Compose.

## Run it
See [README.md](README.md). `docker compose -f deploy/docker-compose.yml up -d`, then `uv run python -m pipeline.run --seed data/seed_npis.json`.
