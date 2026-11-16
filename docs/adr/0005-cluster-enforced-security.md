# ADR 0005: Security is enforced by the cluster, not by scanning user code

Status: accepted (2026-11-16)

**Context.** Heuristics that decide whether code "looks malicious" are bypassable and give false confidence.

**Decision.** The dispatcher builds every pod from one fixed template the user cannot influence
(`dispatcher/podspec.py`; user input enters only as ConfigMap data, the timeout number and a validated run id). The namespace
refuses anything else: Pod Security Admission `restricted`, plus a `ValidatingAdmissionPolicy` requiring a read-only
root filesystem, no service-account token, cpu/memory/ephemeral-storage limits and only `configMap`/`emptyDir` volumes.
Input validation is only: language allowlist, 64 KiB code cap, timeout 1-120 s.

**Consequences.** The guarantees hold even if the dispatcher has a bug, or someone applies a pod by hand. The cost is that the
shared kernel (runc) is the remaining boundary: a kernel exploit is not defended (see the threat model).

**Verified by.** Every line of the template is asserted in `tests/unit/test_podspec.py` and attacked in `tests/attack`; privileged,
hostPath, writable-rootfs and token-mounting pods applied directly are rejected (`test_attacks.py`).
