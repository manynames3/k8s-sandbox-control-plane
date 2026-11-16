# ADR 0007: Spread with topologySpreadConstraints, not hard anti-affinity

Status: accepted (2026-11-16)

**Context.** Sandboxes should spread across nodes, but hard anti-affinity would cap concurrency at the node count.

**Decision.** `topologySpreadConstraints` (maxSkew 1 on hostname, `ScheduleAnyway`) plus resource requests equal to limits
(Guaranteed QoS).

**Consequences.** Spreading is best-effort. Scheduling stays possible when nodes are busy. The spread test reads the worker count and does
not hard-code it.

**Verified by.** The batch of 20 on kind uses both workers (the cluster panel shows both busy); `tests/unit/test_podspec.py`
asserts there is no `affinity`.
