"""Streamlit human-review dashboard.

Shows the review queue, the proposed diff, supporting/conflicting sources, and
confidence — and lets a reviewer approve (apply + version) or reject. Also
surfaces the live cost ledger so the cost-efficiency story is visible.

    uv run streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
import sys

# `streamlit run dashboard/app.py` puts the script dir (not the repo root) on
# sys.path, so add the repo root here to make `db`/`pipeline` importable both
# locally and inside the container.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlmodel import select

TEAL, AMBER, RED, SLATE = "#2dd4bf", "#f59e0b", "#ef4444", "#64748b"


def _load_bakeoff() -> pd.DataFrame | None:
    p = Path(__file__).resolve().parent.parent / "data" / "bakeoff_results.txt"
    if not p.exists():
        return None
    rows = []
    for line in p.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 5 and "/" in parts[0] and parts[1].replace(".", "").isdigit():
            rows.append({"model": parts[0].split("/")[-1], "accuracy": float(parts[1]),
                         "cost_per_1k": float(parts[3])})
    return pd.DataFrame(rows) if rows else None

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
c1.metric("Records processed", sum(r.records_processed or 0 for r in runs))
c2.metric("Auto-updated", sum(r.auto_update or 0 for r in runs))
c3.metric("Pending review", len(pending))
total_cost = sum(row.usd or 0 for row in ledger)
recs = sum(r.records_processed or 0 for r in runs) or 1
c4.metric("Cost / 1k records", f"${total_cost / recs * 1000:.3f}")

# --- decision-outcome donut (the funnel in one glance) ---
no_change = sum(r.no_change or 0 for r in runs)
auto = sum(r.auto_update or 0 for r in runs)
review = sum(r.human_review or 0 for r in runs)
if no_change + auto + review > 0:
    st.markdown("##### Where records land in the funnel")
    donut = go.Figure(go.Pie(
        labels=["Auto-updated", "Human review", "No change"],
        values=[auto, review, no_change], hole=0.6,
        marker=dict(colors=[TEAL, AMBER, SLATE]), sort=False,
        textinfo="label+value"))
    donut.update_layout(height=280, margin=dict(t=20, b=20, l=10, r=10),
                        showlegend=True,
                        legend=dict(orientation="h", yanchor="bottom", y=-0.15,
                                    xanchor="center", x=0.5))
    st.plotly_chart(donut, use_container_width=True)

tab_review, tab_cost, tab_changes = st.tabs(["Review queue", "Cost & models", "Change history"])

with tab_review:
    if not pending:
        st.success("No records pending review. 🎉")
    for item in pending:
        rec = item.recommendation or {}
        with st.expander(f"NPI {item.npi} — {rec.get('reason', '')}  "
                         f"(conf {rec.get('overall_confidence')})"):
            rows = [{
                "field": c.get("field"), "old": c.get("old_value"),
                "new": c.get("new_value"), "confidence": c.get("confidence_score"),
                "sources": ", ".join(c.get("supporting_sources") or []),
                "conflicts": ", ".join(c.get("conflicting_sources") or []),
            } for c in (rec.get("changes") or [])]
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
    m1, m2, m3 = st.columns(3)
    m1.metric("Total spend (all runs)", f"${total_cost:.4f}")
    m2.metric("Cost / 1,000 records", f"${total_cost / recs * 1000:.3f}")
    m3.metric("LLM reconciliations", sum(1 for r in ledger if (r.category or "").startswith("llm")))

    # spend by funnel stage
    if ledger:
        agg: dict[str, list] = {}
        for r in ledger:
            a = agg.setdefault(r.category or "unknown", [0, 0.0])
            a[0] += 1
            a[1] += r.usd or 0
        sdf = pd.DataFrame([{"stage": k, "events": v[0], "usd": round(v[1], 6)}
                            for k, v in agg.items()]).sort_values("usd", ascending=True)
        fig = px.bar(sdf, x="usd", y="stage", orientation="h", text="events",
                     color="usd", color_continuous_scale=["#2dd4bf", "#f59e0b"])
        fig.update_traces(texttemplate="%{text} events", textposition="outside")
        fig.update_layout(height=240, margin=dict(t=30, b=10, l=10, r=10),
                          coloraxis_showscale=False, title="Spend by funnel stage (USD)")
        st.plotly_chart(fig, use_container_width=True)

    # the cost lever: cheapest accurate model wins
    bk = _load_bakeoff()
    if bk is not None:
        st.markdown("**LLM model bake-off — we pick the cheapest model that stays accurate**")
        bk = bk.sort_values("cost_per_1k")
        colors = [TEAL if (a >= 1.0) else SLATE for a in bk["accuracy"]]
        fig2 = go.Figure(go.Bar(
            x=bk["cost_per_1k"], y=bk["model"], orientation="h",
            marker_color=colors,
            text=[f"${c:.3f}/1k · {int(a*100)}% acc" for c, a in zip(bk["cost_per_1k"], bk["accuracy"])],
            textposition="outside"))
        fig2.update_layout(height=300, margin=dict(t=30, b=10, l=10, r=80),
                           title="Cost per 1,000 conflict reconciliations (teal = 100% accurate)",
                           xaxis_title="USD per 1,000")
        st.plotly_chart(fig2, use_container_width=True)
        st.caption("Winner: DeepSeek V4 Flash — 100% accuracy at $0.067 / 1,000 conflicts. "
                   "GLM 5.2 escalation only for the low-confidence residual.")

with tab_changes:
    with get_session() as s:
        changes = s.exec(select(ProposedChange).order_by(ProposedChange.created_at.desc())
                         .limit(200)).all()
    if changes:
        cdf = pd.DataFrame([{
            "npi": c.npi, "field": c.field, "old": c.old_value, "new": c.new_value,
            "confidence": c.field_confidence, "action": c.action,
            "sources": ", ".join(c.supporting_sources or []), "llm": c.llm_tier_used,
        } for c in changes])
        # confidence distribution vs the auto-update threshold
        hist = px.histogram(cdf, x="confidence", color="action", nbins=20,
                            color_discrete_map={"auto_update": TEAL, "human_review": AMBER,
                                                "no_change": SLATE},
                            range_x=[0, 1])
        hist.add_vline(x=0.85, line_dash="dash", line_color=RED,
                       annotation_text="auto-update threshold", annotation_position="top")
        hist.update_layout(height=300, margin=dict(t=30, b=10, l=10, r=10),
                           title="Confidence distribution — only the right tail auto-applies",
                           bargap=0.05)
        st.plotly_chart(hist, use_container_width=True)
        st.dataframe(cdf, use_container_width=True)
    else:
        st.info("No changes recorded yet.")
