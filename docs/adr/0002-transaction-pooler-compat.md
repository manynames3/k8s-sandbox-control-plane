# ADR 0002: Must work through a transaction-mode connection pooler

Status: accepted (2026-11-16)

**Context.** Neon's pooled endpoint is PgBouncer in transaction mode: no `LISTEN/NOTIFY`, no session-level advisory locks,
no session state, and server-side prepared statements are unsafe.

**Decision.** Use polling, and `pg_try_advisory_xact_lock` (released at commit) for the claim, sweeper and retention
locks. psycopg connects with `prepare_threshold=None`. SSE is served by the API polling the events table. The same code runs
unchanged on the in-cluster Postgres and on Neon.

**Consequences.** No push latency optimisation is possible. Nothing in the code may rely on session state; new code needs
to keep that rule (a reviewer should reject `LISTEN`, session advisory locks, temp tables, `SET`).

**Verified by.** Unit and e2e suites run against plain Postgres. **Not yet verified against a real PgBouncer:** that happens in the first
AWS session, which points the system at Neon's pooled endpoint (`docs/AWS.md`).
