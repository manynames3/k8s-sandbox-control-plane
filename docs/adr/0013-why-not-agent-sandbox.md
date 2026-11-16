# ADR 0013: Why not build on kubernetes-sigs/agent-sandbox?

Status: accepted (2026-11-16)

**Context.** [`kubernetes-sigs/agent-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox) (SIG Apps) provides a
`Sandbox` CRD and controller for "isolated, stateful, singleton workloads" with a stable identity: a long-lived single pod
with stable hostname and persistent storage, scheduled deletion (TTL) and, per its docs, pause/resume. An opt-in extension
adds `SandboxTemplate`, `SandboxClaim` (the user-facing request) and `SandboxWarmPool` (pre-warmed pods). Isolation is
delegated to a `RuntimeClass` (gVisor, Kata) and the project is runtime-agnostic. As read on 2026-11-16 (README and docs
overview; its API is `v1beta1`), it describes **no job queue, concurrency cap, run-to-completion semantics, result or log
capture, or cleanup reconciliation**. (Its hibernation features are stated as goals in the README and as built in in the docs;
I did not verify them.)

**Decision.** Do not depend on it in v1.
1. The workload shape differs: this project runs one-shot jobs (submit, run, collect, delete), not long-lived stateful workspaces.
2. The surrounding platform (queue, concurrency cap, three timeout layers, result capture, cleanup reconciliation) would still
   have to be built on top.
3. The learning and proof goal is to build and *attack* the isolation and cleanup layers directly.

**Consequences.** More code to maintain, and a weaker isolation default than a gVisor-backed approach unless the (untested) gVisor
stretch works. **Revisit** if a workload needs long-lived state or warm pools: the dispatcher could create `SandboxClaim`s
instead of Jobs. For a long-lived agent workspace, use agent-sandbox, not this project.

**Correction to the original brief.** The handoff did not mention `SandboxClaim`, the `v1beta1` API or the TTL feature; they
are added above. Nothing in the original description turned out to be wrong.
