"""Reconcile, sweeper and infra-error decisions with a faked Kubernetes client."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from api.models import Run
from dispatcher import core, metrics
from dispatcher.kube import Obj, PodInfo

T = core.Timing(sweep_max_age=600, sweep_grace=300, requeue_grace=10,
                image_error_after=30, schedule_after=120)
NOW = datetime.now(UTC)


class FakeKube:
    def __init__(self, objs=None, pods=None):
        self.objs, self.pods, self.deleted, self.deleted_runs = objs or [], pods or {}, [], []

    def list_objects(self):
        return self.objs

    def list_pods(self):
        return self.pods

    def delete_object(self, kind, name):
        self.deleted.append((kind, name))

    def delete_run(self, run_id):
        self.deleted_runs.append(run_id)


def make_run(engine, status, dispatched_ago=None, started=False, timeout=30, rid=None):
    rid = rid or uuid.uuid4()
    with Session(engine) as s, s.begin():
        s.add(Run(id=rid, language="python3.12", code="x", status=status, timeout_seconds=timeout,
                  dispatched_at=NOW - timedelta(seconds=dispatched_ago) if dispatched_ago else None,
                  started_at=NOW if started else None))
    return rid


def status(engine, rid):
    with Session(engine) as s:
        return s.get_one(Run, rid).status


def job(rid, age=1, failed=None):
    return Obj("jobs", f"run-{rid}", str(rid), NOW - timedelta(seconds=age), failed)


def test_dispatching_run_without_job_is_requeued_after_grace(db):
    rid = make_run(db, "dispatching", dispatched_ago=60)
    core.reconcile(db, FakeKube(), T, NOW)
    assert status(db, rid) == "queued"
    assert metrics.reconcile_corrections["requeue_no_job"] >= 1


def test_fresh_dispatching_run_gets_grace(db):
    rid = make_run(db, "dispatching", dispatched_ago=2)
    core.reconcile(db, FakeKube(), T, NOW)
    assert status(db, rid) == "dispatching"


def test_running_run_without_job_is_infra_error(db):
    rid = make_run(db, "running", dispatched_ago=60, started=True)
    k = FakeKube()
    core.reconcile(db, k, T, NOW)
    assert status(db, rid) == "infra_error"
    assert k.deleted_runs == [str(rid)]


def test_job_for_missing_run_is_deleted(db):
    rid = uuid.uuid4()
    k = FakeKube([job(rid), Obj("pods", f"run-{rid}-x", str(rid), NOW)])
    core.reconcile(db, k, T, NOW)
    assert k.deleted_runs == [str(rid)] and ("pods", f"run-{rid}-x") in k.deleted


def test_job_for_cancelled_run_is_deleted(db):
    rid = make_run(db, "cancelled", dispatched_ago=5)
    k = FakeKube([job(rid)])
    core.reconcile(db, k, T, NOW)
    assert k.deleted_runs == [str(rid)]


def test_job_for_live_run_is_left_alone(db):
    rid = make_run(db, "running", dispatched_ago=5, started=True)
    k = FakeKube([job(rid)])
    core.reconcile(db, k, T, NOW)
    assert k.deleted_runs == [] and status(db, rid) == "running"


def test_unlabelled_object_is_not_reconciles_business(db):
    k = FakeKube([Obj("jobs", "hand-made", None, NOW)])
    core.reconcile(db, k, T, NOW)
    assert k.deleted_runs == [] and k.deleted == []


def test_sweeper_deletes_after_timeout_plus_grace(db):
    rid = make_run(db, "running", dispatched_ago=5, started=True, timeout=30)
    old, young = job(rid, age=30 + 300 + 5), job(uuid.uuid4(), age=100)
    k = FakeKube([old, young])
    assert core.sweep(db, k, T, NOW) == 1
    assert k.deleted == [("jobs", old.name)]


def test_sweeper_catches_unlabelled_objects_after_max_age(db):
    k = FakeKube([Obj("jobs", "hand-made", None, NOW - timedelta(seconds=601)),
                  Obj("configmaps", "fresh", None, NOW - timedelta(seconds=5))])
    before = metrics.sweeper_deletions["jobs"]
    assert core.sweep(db, k, T, NOW) == 1
    assert metrics.sweeper_deletions["jobs"] == before + 1


def test_observe_fails_image_pull_errors_after_threshold(db):
    rid = make_run(db, "dispatching", dispatched_ago=45)
    pod = PodInfo("p", str(rid), "Pending", None, None, None, None, "ImagePullBackOff", NOW)
    k = FakeKube(pods={str(rid): pod})
    k.delete_run = lambda r: k.deleted_runs.append(r)
    core.observe(db, k, T, NOW)
    assert status(db, rid) == "infra_error" and k.deleted_runs == [str(rid)]


def test_observe_waits_for_slow_scheduling_then_gives_up(db):
    young = make_run(db, "dispatching", dispatched_ago=20)
    old = make_run(db, "dispatching", dispatched_ago=200)
    pods = {str(r): PodInfo("p", str(r), "Pending", None, None, None, None, "ContainerCreating", NOW)
            for r in (young, old)}
    core.observe(db, FakeKube(pods=pods), T, NOW)
    assert status(db, young) == "dispatching" and status(db, old) == "infra_error"


def test_job_deadline_exceeded_without_pod_is_timed_out(db):
    rid = make_run(db, "running", dispatched_ago=60, started=True)
    k = FakeKube([job(rid, failed="DeadlineExceeded")])
    core.observe(db, k, T, NOW)
    assert status(db, rid) == "timed_out"
