import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.models import Run, RunEvent
from dispatcher import core


def run_with_events(engine, finished, age_days):
    rid = uuid.uuid4()
    ts = datetime.now(UTC) - timedelta(days=age_days)
    with Session(engine) as s, s.begin():
        s.add(Run(id=rid, language="python3.12", code="x", status="succeeded" if finished else "running",
                  stdout="kept on the run row", finished_at=ts if finished else None))
        s.flush()
        for kind, data in (("state", {"status": "queued"}), ("stdout", {"text": "a", "line": 0}),
                           ("stderr", {"text": "b", "line": 1})):
            s.add(RunEvent(run_id=rid, kind=kind, data=data, ts=ts))
    return rid


def kinds(engine, rid):
    with engine.connect() as c:
        return sorted(r[0] for r in c.execute(text("select kind from run_events where run_id=:i"), {"i": rid}))


def test_old_log_chunks_of_finished_runs_are_pruned_but_state_and_result_stay(db):
    old, fresh, unfinished = (run_with_events(db, True, 10), run_with_events(db, True, 1),
                              run_with_events(db, False, 10))
    assert core.prune_old_logs(db, retention_days=7) == 2
    assert kinds(db, old) == ["state"]
    assert kinds(db, fresh) == ["state", "stderr", "stdout"]
    assert kinds(db, unfinished) == ["state", "stderr", "stdout"]  # never prune a run still in flight
    with db.connect() as c:
        assert c.execute(text("select stdout from runs where id=:i"), {"i": old}).scalar() == "kept on the run row"


def test_prune_is_idempotent(db):
    run_with_events(db, True, 30)
    assert core.prune_old_logs(db, 7) == 2
    assert core.prune_old_logs(db, 7) == 0
