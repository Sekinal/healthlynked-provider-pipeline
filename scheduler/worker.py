"""Periodic worker: the 'repeatable pipeline' loop.

Stage 0 of the funnel — selects stale/risky records from the DB, then runs the
pipeline on just those. Runs on an interval via APScheduler. In production this
is the always-on container; for a one-shot run use `run_once()`.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlmodel import select

from db.models import Provider
from db.session import get_session, init_db
from pipeline import audit
from pipeline.config import settings
from pipeline.run import process_record
from pipeline.schemas import Action, ProviderRecord
from pipeline.stage0_staleness import is_stale

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")


def select_stale() -> list[ProviderRecord]:
    with get_session() as s:
        provs = s.exec(select(Provider)).all()
    records = [
        ProviderRecord(
            provider_id=p.provider_id, npi=p.npi, provider_name=p.provider_name,
            specialty=p.specialty, practice_name=p.practice_name, address=p.address,
            phone=p.phone, website=p.website, active_status=p.active_status,
            last_verified_date=p.last_verified_date,
        )
        for p in provs
    ]
    return [r for r in records if is_stale(r)]


def run_once(enrich: bool = True, reconcile: bool = True) -> dict:
    init_db()
    records = select_stale()
    log.info("stale records selected: %d", len(records))
    if not records:
        return {"processed": 0}
    run_id = audit.start_run(notes="scheduled run")
    counts = {Action.no_change: 0, Action.auto_update: 0, Action.human_review: 0}
    for rec in records:
        out = process_record(rec, run_id, enrich=enrich, reconcile=reconcile)
        counts[out.recommended_action] += 1
        log.info("%s -> %s", rec.npi, out.recommended_action.value)
    audit.finish_run(run_id, len(records), counts[Action.no_change],
                     counts[Action.auto_update], counts[Action.human_review])
    return {"processed": len(records), **{k.value: v for k, v in counts.items()}}


def main(interval_minutes: int = 1440) -> None:
    init_db()
    sched = BlockingScheduler()
    sched.add_job(run_once, "interval", minutes=interval_minutes, next_run_time=None)
    log.info("scheduler started; interval=%dm (stale_after=%dd)",
             interval_minutes, settings.stale_after_days)
    sched.start()


if __name__ == "__main__":
    import sys

    if "--once" in sys.argv:
        print(run_once())
    else:
        main()
