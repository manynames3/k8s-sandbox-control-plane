# ADR 0006: The dispatcher is a reconciling controller

Status: accepted (2026-11-16)

**Context.** The platform must converge after crashes: a run cannot be lost between "claimed" and "Job created", and nothing
may run twice or leak.

**Decision.** The database holds intent, the cluster holds reality, and the dispatcher converges them. Job names are
deterministic (`run-<id>`), so creation is idempotent. Cleanup has four layers: the wrapper timeout, the Job's
`activeDeadlineSeconds`, reconcile (a live run with no Job is re-queued or marked `infra_error`; a cluster object whose
run is gone or terminal is deleted) and the sweeper (age-based, last resort, counted in a metric: non-zero means an earlier
layer failed). The result is persisted before the cluster objects are deleted.

**Consequences.** Observation is a 0.5 s poll rather than a Kubernetes watch (simpler and robust under chaos); a watch could
replace it if latency mattered. Reconcile and the sweeper add list calls per tick.

**Verified by.** `tests/unit/test_reconcile.py`, e2e (job deleted mid-run becomes `infra_error`, hand-made orphan removed by the sweeper),
chaos (dispatcher killed twice with 10 runs in flight: one terminal state each, none ran twice, `sandboxes` ends empty).
