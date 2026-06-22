"""Streamlit human-review dashboard.

Shows the review queue, the proposed diff, supporting/conflicting sources, and
confidence — and lets a reviewer approve (apply + version) or reject. Also
surfaces the live cost ledger so the cost-efficiency story is visible.

    uv run streamlit run dashboard/app.py
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from sqlmodel import select

from db.models import CostLedger, Provider, ProviderVersion, ProposedChange, ReviewItem, Run
from db.session import get_session, init_db

st.set_page_config(page_title="HealthLynked Directory Review", layout="wide")
init_db()


def _apply_approval(item: ReviewItem, reviewer: str, notes: str) -> None:
    with get_session() as s:
        db_item = s.get(ReviewItem, item.id)
        db_item.status = "approved"
        db_item.reviewer = reviewer
        db_item.notes = notes
        db_item.resolved_at = datetime.now(timezone.utc)
        prov = s.get(Provider, db_item.npi)
        rec = db_item.recommendation
        if prov:
            for ch in rec.get("changes", []):
                setattr(prov, ch["field"], ch["new_value"])
            prov.updated_at = datetime.now(timezone.utc)
            s.add(prov)
            n = len(s.exec(select(ProviderVersion).where(ProviderVersion.npi == db_item.npi)).all())
            s.add(ProviderVersion(npi=db_item.npi, version=n + 1,
                                  snapshot=prov.model_dump(mode="json"),
                                  changed_fields=[c["field"] for c in rec.get("changes", [])],
                                  run_id=db_item.run_id))
        s.add(db_item)
        s.commit()


def _reject(item: ReviewItem, reviewer: str, notes: str) -> None:
    with get_session() as s:
        db_item = s.get(ReviewItem, item.id)
        db_item.status = "rejected"
        db_item.reviewer = reviewer
        db_item.notes = notes
        db_item.resolved_at = datetime.now(timezone.utc)
        s.add(db_item)
        s.commit()


st.title("🩺 HealthLynked — Provider Directory Review")

# --- top metrics ---
with get_session() as s:
    runs = s.exec(select(Run)).all()
    pending = s.exec(select(ReviewItem).where(ReviewItem.status == "pending")).all()
    ledger = s.exec(select(CostLedger)).all()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Records processed", sum(r.records_processed for r in runs))
c2.metric("Auto-updated", sum(r.auto_update for r in runs))
c3.metric("Pending review", len(pending))
total_cost = sum(row.usd for row in ledger)
recs = sum(r.records_processed for r in runs) or 1
c4.metric("Cost / 1k records", f"${total_cost / recs * 1000:.3f}")

tab_review, tab_cost, tab_changes = st.tabs(["Review queue", "Cost ledger", "Change history"])

with tab_review:
    if not pending:
        st.success("No records pending review. 🎉")
    for item in pending:
        rec = item.recommendation
        with st.expander(f"NPI {item.npi} — {rec.get('reason', '')}  "
                         f"(conf {rec.get('overall_confidence')})"):
            rows = [{
                "field": c["field"], "old": c.get("old_value"),
                "new": c.get("new_value"), "confidence": c.get("confidence_score"),
                "sources": ", ".join(c.get("supporting_sources", [])),
                "conflicts": ", ".join(c.get("conflicting_sources", [])),
            } for c in rec.get("changes", [])]
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
            reviewer = st.text_input("Reviewer", key=f"rev_{item.id}", value="analyst")
            notes = st.text_input("Notes", key=f"note_{item.id}")
            col_a, col_r = st.columns(2)
            if col_a.button("✅ Approve & apply", key=f"app_{item.id}"):
                _apply_approval(item, reviewer, notes)
                st.rerun()
            if col_r.button("❌ Reject", key=f"rej_{item.id}"):
                _reject(item, reviewer, notes)
                st.rerun()

with tab_cost:
    if ledger:
        df = pd.DataFrame([{"category": r.category, "usd": r.usd, "detail": r.detail}
                           for r in ledger])
        st.bar_chart(df.groupby("category")["usd"].sum())
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No cost rows yet. Run the pipeline first.")

with tab_changes:
    with get_session() as s:
        changes = s.exec(select(ProposedChange).order_by(ProposedChange.created_at.desc())
                         .limit(200)).all()
    if changes:
        st.dataframe(pd.DataFrame([{
            "npi": c.npi, "field": c.field, "old": c.old_value, "new": c.new_value,
            "confidence": c.field_confidence, "action": c.action,
            "sources": ", ".join(c.supporting_sources), "llm": c.llm_tier_used,
        } for c in changes]), use_container_width=True)
    else:
        st.info("No changes recorded yet.")
