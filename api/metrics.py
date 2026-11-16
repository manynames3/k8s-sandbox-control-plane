"""Prometheus metrics for the control plane, computed from the database at scrape time.

The runs table already records every timestamp, so counters and histograms are derived from it:
they survive API restarts and agree across replicas by construction. Names are prefixed `ksc_`.
"""
import os
from collections.abc import Iterable

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily
from prometheus_client.metrics_core import Metric
from prometheus_client.registry import Collector
from sqlalchemy import text
from sqlalchemy.orm import Session

from api import states
from api.db import get_engine

BUCKETS = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0]

# name, help, SQL expression in seconds, WHERE clause
HISTOGRAMS = [
    ("ksc_run_queue_wait_seconds", "Time from submission to the dispatcher claiming the run",
     "EXTRACT(EPOCH FROM (dispatched_at - created_at))", "dispatched_at IS NOT NULL"),
    ("ksc_run_schedule_seconds", "Time from the Job being created to the container running",
     "EXTRACT(EPOCH FROM (started_at - dispatched_at))", "started_at IS NOT NULL AND dispatched_at IS NOT NULL"),
    ("ksc_run_exec_seconds", "Time the container spent running",
     "EXTRACT(EPOCH FROM (finished_at - started_at))", "finished_at IS NOT NULL AND started_at IS NOT NULL"),
    ("ksc_run_e2e_seconds", "Time from submission to a terminal state",
     "EXTRACT(EPOCH FROM (finished_at - created_at))", "finished_at IS NOT NULL"),
    ("ksc_sandbox_lifetime_seconds", "Time the sandbox objects existed (Job created to result recorded)",
     "EXTRACT(EPOCH FROM (finished_at - dispatched_at))", "finished_at IS NOT NULL AND dispatched_at IS NOT NULL"),
]


def _histogram_sql(expr: str, where: str) -> str:
    buckets = ", ".join(f"count(*) FILTER (WHERE {expr} <= {b})" for b in BUCKETS)
    return f"SELECT count(*), coalesce(sum({expr}), 0), {buckets} FROM runs WHERE {where}"


class DbCollector(Collector):
    def collect(self) -> Iterable[Metric]:
        terminal = ",".join(f"'{s}'" for s in sorted(states.TERMINAL))
        with Session(get_engine()) as s:
            submitted = CounterMetricFamily("ksc_runs_submitted", "Runs submitted", labels=["language"])
            for lang, n in s.execute(text("SELECT language, count(*) FROM runs GROUP BY language")):
                submitted.add_metric([lang], n)
            yield submitted

            finished = CounterMetricFamily("ksc_runs_finished", "Runs that reached a terminal state",
                                           labels=["language", "status"])
            for lang, status, n in s.execute(text(
                    f"SELECT language, status, count(*) FROM runs WHERE status IN ({terminal}) "
                    "GROUP BY language, status")):
                finished.add_metric([lang, status], n)
            yield finished

            counts: dict[str, int] = dict(s.execute(text("SELECT status, count(*) FROM runs WHERE status IN "
                                         "('queued','dispatching','running') GROUP BY status")).all())
            yield GaugeMetricFamily("ksc_runs_active", "Runs dispatching or running",
                                    value=counts.get("dispatching", 0) + counts.get("running", 0))
            yield GaugeMetricFamily("ksc_queue_depth", "Runs waiting to be claimed", value=counts.get("queued", 0))
            yield GaugeMetricFamily("ksc_concurrency_cap", "Maximum concurrent sandboxes",
                                    value=int(os.environ.get("CONCURRENCY_CAP", "4")))

            for name, doc, expr, where in HISTOGRAMS:
                row = s.execute(text(_histogram_sql(expr, where))).one()
                total, total_sum, per_bucket = row[0], float(row[1]), row[2:]
                h = HistogramMetricFamily(name, doc)
                h.add_metric([], buckets=[(str(b), n) for b, n in zip(BUCKETS, per_bucket, strict=True)]
                             + [("+Inf", total)], sum_value=total_sum)
                yield h
