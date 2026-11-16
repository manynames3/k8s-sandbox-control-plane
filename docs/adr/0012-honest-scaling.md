# ADR 0012: An honest scaling story

Status: accepted (2026-11-16)

**Context.** On kind the node count is fixed, so "autoscaling" could be faked easily.

**Decision.** Demonstrate only what is real: queue backpressure, a concurrency cap (the ResourceQuota pod count), and KEDA
scaling *dispatcher replicas* on queue depth. Node autoscaling (Karpenter) is described in `docs/AWS.md` and is not built.

**Consequences.** The benchmark at client concurrency 20 shows queueing, not parallelism: the cap is the bottleneck
(see the measured results). KEDA's `cooldownPeriod` governs only scale-to-zero; scaling from N to 1 follows the HPA's
stabilization window, which is set explicitly (found by the e2e test).

**Verified by.** `tests/e2e/test_keda.py`.
