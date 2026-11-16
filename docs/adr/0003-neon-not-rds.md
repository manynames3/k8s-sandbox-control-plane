# ADR 0003: Neon instead of RDS for cloud sessions

Status: accepted (2026-11-16)

**Context.** Ephemeral EKS sessions need a database that outlives the cluster. RDS costs money while idle and takes
minutes to create, against an idle target of under $0.10/month.

**Decision.** Neon (free plan) in cloud sessions. It suspends when idle, so it costs nothing between sessions, and it
keeps run history across clusters. The dispatcher only polls while a cluster exists. To stay inside the free plan's
storage, the dispatcher prunes output chunks of finished runs older than `logRetentionDays` (default 7).

**Consequences.** A cold database wakes slowly (the connect timeout is 10 s on EKS versus 3 s on kind). Free-plan limits change;
`docs/COST.md` records what was read on 2026-11-16 and says to re-check. A real deployment would use a managed multi-AZ database.

**Verified by.** `tests/unit/test_retention.py` for the pruning. The Neon path itself is **untested** until the AWS session.
