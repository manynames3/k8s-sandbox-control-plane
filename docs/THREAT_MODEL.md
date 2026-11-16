# Threat model

## Scope

The system runs **untrusted code submitted by an AI agent or a person** in short-lived Kubernetes sandboxes. This
document says what is protected, who the attacker is, which control answers each threat, which test proves it, and what is
**not** defended.

## Assets

1. **The host kernel and the nodes** (a shared kernel under runc).
2. **Other tenants' runs**: their code, files and results.
3. **Credentials**: the database URL, the API keys, the dispatcher's Kubernetes token, cloud instance credentials (EKS).
4. **The control plane's availability** (the queue, the dispatcher, the API).
5. **Cost**: AWS spend.

## Attacker

Arbitrary code running **inside a sandbox**, with the permissions of an unprivileged user (UID 10001) in a container that has no
capabilities. The attacker controls the code, its stdin and the timeout (1-120 s), and can submit many runs through a valid API key.
Out of scope as an attacker: someone with cluster-admin, a compromised dispatcher, a compromised `main` branch.

## Threats, controls and proof

| # | Threat | Control | Proving test | Residual risk |
|---|---|---|---|---|
| 1 | Reach the internet, DNS, the Kubernetes API, another pod or the cloud metadata address | Default-deny NetworkPolicy (ingress and egress, DNS included) in `sandboxes`; EKS also IMDSv2 required with hop limit 1 | `test_network_is_fully_denied_but_run_completes` (with a loopback positive control); EKS: `test_pod_network_cannot_reach_instance_metadata_but_host_network_can` | Enforcement depends on the CNI. Verified on Calico (kind); the EKS VPC CNI agent is **untested** until the first AWS session. Some CNIs do not enforce policy on host-network paths |
| 2 | Steal a service-account token | `automountServiceAccountToken: false`, a service account with zero RBAC, enforced by an admission policy | `test_service_account_token_does_not_exist`, `test_policy_denies_writable_rootfs_and_token_even_when_psa_passes` | The `KUBERNETES_*` address variables are always injected (an address, not a credential) |
| 3 | Write to the node or image filesystem | Read-only root filesystem; only a size-limited `emptyDir` at `/tmp`; enforced by admission | `test_cannot_write_outside_tmp` (errno 30), `test_filling_tmp_is_contained_and_reported` | The `/tmp` limit is enforced by kubelet eviction (about 45 s), not instantly |
| 4 | Exhaust memory | Memory limit = request (Guaranteed QoS) | `test_memory_past_the_limit_is_oom_killed` | None observed |
| 5 | Fork bomb | Kubelet `podPidsLimit` 256 (kind config; on EKS set through the sandbox node group's NodeConfig, **unverified until the AWS session**) | `test_pid_limit_stops_forking_at_the_cap`, `test_fork_bomb_is_contained_and_neighbours_still_run` | The EKS NodeConfig setting was validated by Terraform but not applied; the same attack test will fail there if it did not take effect |
| 6 | Infinite loop / run past its timeout | Three layers: wrapper kill at the timeout (distinct exit 124), Job `activeDeadlineSeconds`, sweeper | `test_infinite_loop_times_out_at_about_the_timeout` | Layer 2 is only unit-tested with fakes (the wrapper always fires first) |
| 7 | Flood the control plane with output (bytes, frames, NUL bytes) | Wrapper caps each stream at 1 MiB (excess drained); dispatcher reads logs with a byte limit, caps again, replaces NUL bytes (Postgres rejects them), stores at most 4000 chunk rows per run in batches of 500, and isolates a failing run from the others | `test_unbounded_output_is_truncated_and_dispatcher_memory_stays_flat`, `test_nul_bytes_in_output_cannot_wedge_the_dispatcher`, `tests/unit/test_review_fixes.py` | Memory is measured as RSS delta (< 50 MB), not a profile; the dispatcher still re-reads up to 4 MiB of log per running pod per tick |
| 8 | Read another concurrent run's code or files | Separate pods, mounts and ConfigMaps; code arrives only as a per-run read-only ConfigMap | `test_cannot_find_another_concurrent_runs_code_or_files` (scans the filesystem and `/proc` with a positive control) | A shared kernel: see below |
| 9 | Privilege escalation inside the container | Non-root, `allowPrivilegeEscalation: false`, all capabilities dropped, `RuntimeDefault` seccomp | `test_no_capabilities_no_privilege_no_mount_chroot_or_raw_sockets` | Kernel bugs (below) |
| 10 | Bypass the dispatcher: apply a privileged / hostPath / writable-rootfs pod directly | Pod Security Admission `restricted` plus a `ValidatingAdmissionPolicy` (also: image registry allowlist, no ephemeral containers, and on EKS the sandbox node pool and runtime class) | `test_privileged_pod_applied_directly_is_rejected`, `test_hostpath_pod_applied_directly_is_rejected`, `test_policy_denies_writable_rootfs_and_token_even_when_psa_passes`, `test_policy_denies_images_from_unapproved_registries` | An actor who can modify the policy or the namespace labels. The node-pool and runtime-class rules only exist where those are configured (EKS) and are untested there. `hostUsers` and the ephemeral-storage limit size are not constrained |
| 11 | Leave resources behind (cost, resource exhaustion) | Result persisted first, then deletion; reconcile and sweeper; owner references | cleanup proof after every terminal state in `tests/e2e`; `test_sweeper_removes_hand_made_orphans`; chaos suite | The sweeper's age thresholds (timeout + 5 min, 10 min) bound how long a leak can live |
| 12 | Crash the dispatcher / the database mid-flight | Idempotent creation, reconcile, guarded state transitions, readiness that reports the database | `test_killing_the_dispatcher_with_10_runs_in_flight`, `test_database_interruption_degrades_readiness_and_recovers` | A run whose Job disappears after it was created (dispatcher down longer than the 1-hour Job TTL, or the sweeper) becomes `infra_error` and is **never re-run**. Kubernetes can still, rarely, start a Job's pod twice; that is not prevented |
| 13 | Run up an AWS bill | Cost-trap guards in CI, an ephemeral cluster whose expiry is a tag plus a reaper that runs every hour, budget alerts, a GitHub Environment that needs approval (a setting in GitHub, not enforced by this code) | `tests/unit/test_infra_guards.py`; **the session and reaper workflows are unrun** | Budget alerts only notify; the reaper can stop running in an inactive repository |

## Deliberately not defended

- **Kernel exploits.** Sandboxes share the host kernel (runc). A kernel or container-runtime vulnerability is an escape. gVisor or
  Firecracker/Kata would reduce this; a gVisor attempt exists (`deploy/eks/gvisor.sh`) but has **never been run**.
- **Side channels** (timing, cache, CPU frequency) between co-located sandboxes.
- **A compromised dispatcher.** It holds namespaced rights to create and delete Jobs in `sandboxes`, and database access. The
  database holds all code and results. It runs in the trusted namespace, which sandboxes cannot reach.
- **A compromised API key.** It can submit work within its limits (default 600 submissions per minute and 200 runs in flight per key, enforced with HTTP 429) and hold up to 8 event streams. A key can still fill the queue up to its cap; there is no fairness between keys.
- **Multi-tenancy.** The design is single-tenant. Different tenants' code would share nodes and a database.
- **Denial of service of the queue.** One key can fill the queue; there is no fairness between keys.
- **Supply chain of the base images.** Images are pinned by digest and scanned (trivy, CRITICAL, fixed only); that reduces but does not
  remove this risk.
- **Exposure of the API.** The execution API is never to be exposed to the internet; it has no per-request abuse protection beyond keys.

## Trust boundaries

`ksc-system` (API, dispatcher, local Postgres) is trusted; `sandboxes` holds only untrusted pods. The dispatcher's RBAC is a
namespaced Role in `sandboxes` (jobs, pods, pod logs, configmaps, events; no secrets, nothing cluster-wide). It was extended
beyond the original brief with `list` on configmaps and `delete` on pods, so the sweeper can find orphans.

## Independent review (2026-11-16)

A fresh agent that had not seen the build reviewed the dispatcher, API, chart, Terraform, workflows and runner. Outcome:

**Fixed (each with a regression test):** a NUL byte in output could make every database write for a run fail and hold a slot until
the sweeper fired; frame floods could break the insert; one failing run could stop the others being observed and skip reconcile;
a run whose Job vanished could be re-queued and executed twice (a `job_created_at` marker now prevents re-queueing, and the Job
TTL is 1 hour); per-key rate and in-flight limits were never enforced; no limit on event streams or request bodies, and the
liveness probe shared the thread pool with SSE; a single-run idempotency key could collide with a batch item; the CI role could
create a role with an admin inline policy (`iam:PutRolePolicy` and `iam:UpdateAssumeRolePolicy` removed); the admission policy did
not constrain images, node placement, runtime class or ephemeral containers; the teardown could lack credentials after a long
session; the reaper could destroy on a failed check; account ids went into public issues.

**Accepted and documented, not fixed:** `/metrics` is unauthenticated and cluster-internal; each event stream holds a worker thread
(capped per key, the pool is shared); on EKS the VPC CNI's default "standard" mode may leave a short window at pod start before a
policy applies (strict mode was not enabled because it can break system pods and I could not test it); the trusted namespace's
egress allows any public address on the database port (the provider's addresses are not stable) and, on kind, the API server on any
address; API and dispatcher share one database role; GitHub Actions are pinned by tag, not commit; the session role's credentials are
visible to the test code that runs in the same job (so code in the repository is trusted with them); `ec2:*` and `eks:*` remain broad
inside one region; the branch restriction of the approval environment is a GitHub setting; `deploy/eks/gvisor.sh` downloads the
latest runsc without a checksum; a program can forge the wrapper's output framing for its own run; with several dispatcher replicas
a late-committing event row can be skipped by a client that already moved past it; `/v1/cluster` shows global counts to any key.
