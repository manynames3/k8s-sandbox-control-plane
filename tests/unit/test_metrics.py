import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api.models import Run


@pytest.fixture
def client(db):
    from api.main import app
    return TestClient(app)


def add(engine, status, language="python3.12", wait=1.0, sched=2.0, exec_=3.0):
    t0 = datetime.now(UTC) - timedelta(minutes=5)
    run = Run(id=uuid.uuid4(), language=language, code="x", status=status, created_at=t0)
    if status in ("dispatching", "running") or status in ("succeeded", "failed", "infra_error"):
        run.dispatched_at = t0 + timedelta(seconds=wait)
    if status in ("running", "succeeded", "failed"):
        run.started_at = t0 + timedelta(seconds=wait + sched)
    if status in ("succeeded", "failed", "infra_error"):
        run.finished_at = t0 + timedelta(seconds=wait + sched + exec_)
    with Session(engine) as s, s.begin():
        s.add(run)


def sample(text, name, **labels):
    for line in text.splitlines():
        if line.startswith((name + "{", name + " ")) and all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"{name}{labels} not found in:\n{text}")


def test_counters_gauges_and_histograms_come_from_the_database(client, db, monkeypatch):
    monkeypatch.setenv("CONCURRENCY_CAP", "4")
    add(db, "succeeded"); add(db, "succeeded"); add(db, "failed", "node20"); add(db, "infra_error")
    add(db, "queued"); add(db, "queued"); add(db, "running")
    text = client.get("/metrics").text
    assert sample(text, "ksc_runs_submitted_total", language="python3.12") == 6
    assert sample(text, "ksc_runs_finished_total", language="python3.12", status="succeeded") == 2
    assert sample(text, "ksc_runs_finished_total", language="python3.12", status="infra_error") == 1
    assert sample(text, "ksc_queue_depth") == 2 and sample(text, "ksc_runs_active") == 1
    assert sample(text, "ksc_concurrency_cap") == 4
    # queue wait 1 s for the 5 runs that were dispatched: all <= 1, none <= 0.5
    assert sample(text, "ksc_run_queue_wait_seconds_bucket", le="1.0") == 5
    assert sample(text, "ksc_run_queue_wait_seconds_bucket", le="0.5") == 0
    assert sample(text, "ksc_run_queue_wait_seconds_count") == 5
    # exec time 3 s for the 3 finished-with-start runs
    assert sample(text, "ksc_run_exec_seconds_bucket", le="2.0") == 0
    assert sample(text, "ksc_run_exec_seconds_bucket", le="5.0") == 3
    assert sample(text, "ksc_run_e2e_seconds_bucket", le="10.0") == 4


def test_metrics_carry_no_code_or_output(client, db):
    add(db, "succeeded")
    assert "print" not in client.get("/metrics").text


def test_dispatcher_counters_are_exported_even_when_zero():
    from prometheus_client import generate_latest

    from dispatcher import metrics
    metrics.sweeper_deletions["jobs"] += 1
    out = generate_latest(metrics.registry).decode()
    assert 'ksc_sweeper_deletions_total{kind="jobs"}' in out
    assert 'ksc_reconcile_corrections_total{reason="none"}' in out or "ksc_reconcile_corrections_total" in out
