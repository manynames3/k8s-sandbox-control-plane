# ADR 0001: Postgres is both the database and the queue

Status: accepted (2026-11-16)

**Context.** Runs must be accepted durably and executed at most once, with a concurrency cap, across several
dispatcher replicas. A separate broker (Redis, SQS) means a dual write: a run can be saved but never enqueued, or
enqueued but never saved.

**Decision.** A run row *is* its queue entry. The API inserts it as `queued`; dispatchers claim work with
`SELECT ... FOR UPDATE SKIP LOCKED`, inside a transaction that first takes `pg_try_advisory_xact_lock` so the count of
active runs and the claim are one atomic step (`dispatcher/core.py: claim`). No Redis.

**Consequences.** One system to run and back up; no lost job between "save" and "enqueue"; claims are disjoint across
replicas. Polling replaces push notifications, so there is a small latency floor (the dispatcher loop is 0.5 s). Throughput
is bounded by Postgres, which is far above what the cluster can execute.

**Verified by.** `tests/unit/test_claim.py` (cap respected under 8 concurrent claimers, FIFO, active runs counted); `tests/e2e/test_keda.py`
(several dispatcher replicas: cap never exceeded, each Job created exactly once); `tests/chaos` (dispatcher killed with runs in flight).
