# ADR 0004: The sandbox never talks to the platform

Status: accepted (2026-11-16)

**Context.** Untrusted code must not be able to reach credentials, the API, the database or the Kubernetes API.

**Decision.** Sandbox pods have no network (default-deny NetworkPolicy including DNS), no service-account token, no
secrets, and a service account with zero RBAC. The dispatcher collects results from outside: exit code and termination reason
from container status, stdout/stderr from the Kubernetes log API with a byte limit. The runner wraps output in JSON lines
so stdout and stderr can be separated from one log stream.

**Consequences.** A sandbox cannot call back, stream metrics or fetch dependencies: runs are self-contained. The
`KUBERNETES_*` service address variables are still injected by the kubelet (an address, not a credential; the
network denial is what makes it harmless).

**Verified by.** `tests/attack` (network, token, filesystem, capabilities) and `scripts/verify-cluster.sh` with positive controls.
