# ADR 0009: Ephemeral EKS with an enforced lifetime

Status: accepted (2026-11-16)

**Context.** Real-AWS proof is valuable; an always-on cluster is expensive and a forgotten one is dangerous.

**Decision.** The session workflow creates, tests, measures and destroys in one run, with destroy in an `if: always()` step,
an orphan check that fails loudly, and an independent reaper workflow as a backstop. Budget alerts are the final backstop
(GitHub pauses scheduled workflows in inactive repositories). Cost traps are designed out and guarded in CI.

**Consequences.** The proof environment exists for hours, not months. Costs are bounded and documented (`docs/COST.md`).
**Status:** written and statically validated; not yet run.
