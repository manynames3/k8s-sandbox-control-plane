# PROGRESS

## Phase 0 — Preflight (2026-11-16, started)

**Done:** repo scaffold (layout dirs, `git init`, `.gitignore`), `docs/HANDOFF.md`, `CLAUDE.md`, 13 ADR stubs, `Makefile` with `doctor`/`disk`/`clean`/`nuke`, `scripts/doctor.sh`.

**Environment fix (2026-11-16):** `/Volumes/CRUCIAL 525GB` is a read-only macOS system volume; the writable half is `/Volumes/CRUCIAL 525GB - Data` (holds `Users/Codex`). `scripts/mac-setup.sh` (adapted from the owner's script) puts `cx` at `.../Data/Users/Codex/cx`, addressed via the space-free symlink `/Users/Shared/ksc-cx` (`$CX`), installed colima/kind/trivy/kubeconform, started Colima (VM on the drive), switched docker context to colima.
**Verified:** `make doctor` PASS with `source /Users/Shared/ksc-cx/env.sh` (cgroup v2, colima context, all tools, drive 168 GB / internal 13 GB free). Internal disk spots unchanged (Caches 3.3G, .cache 2.9G, .npm 2.0G; pre-existing).
**Caveats:** drive does not report as SSD (USB, "Info not available"); watch kind stability/perf. Colima VM: 4 CPU / 8 GB / 60 GB sparse.

**Next:** owner checkpoint A (repo name, public, worker count), commit phase 0, then phase 1.

## Phase 1 — Cluster, chart, Postgres, migrations, API health (2026-11-16, ~1.5 h)

**Done:**
- kind v0.33.0, node `kindest/node:v1.36.4` pinned by digest, `WORKERS=2` default (1 control-plane + 2 workers; see decision below), default CNI off, Calico v3.33.1 (vendored manifest), kubelet `podPidsLimit: 256`. Config generated from `deploy/kind/kind-config.yaml.tmpl` by `deploy/kind/up.sh`.
- Helm chart `charts/ksc`: `sandboxes` namespace with PSA `restricted`; dispatcher namespaced Role/RoleBinding (no secrets, nothing cluster-wide); zero-RBAC `sandbox` SA without token; default-deny NetworkPolicy (DNS included) in `sandboxes`, optional egress allowlist (excludes 169.254/16 and private ranges); ResourceQuota (pods = concurrencyCap, default 4) and LimitRange; trusted `ksc-system` netpol; in-cluster Postgres 16 (password generated once via `lookup`); API Deployment with Alembic init container.
- API: FastAPI `/healthz`, `/readyz` (503 when DB unreachable); SQLAlchemy models + initial Alembic migration (`api_keys`, `runs`, `run_events`); state machine module with unit tests; psycopg with `prepare_threshold=None` for transaction-pooler compatibility.
- `make up|down|test|lint|verify-cluster|doctor|disk|clean|nuke`.

**Verified how:**
- `make down` then `make up` from clean, twice. `down` leaves no kind clusters or containers.
- `make verify-cluster` PASS: 3 nodes Ready, Calico on all, PSA label, quota, RBAC `can-i` checks, PSA rejects a privileged pod, and NetworkPolicy enforcement (sandbox pod cannot resolve DNS, reach 1.1.1.1 or reach Postgres), with positive controls from `ksc-system` so the denials are not vacuous (a first version lacked these and could have passed with a dead pod; fixed).
- `make test` 8 passed; ruff and mypy clean. `/healthz` ok and `/readyz` 200 via port-forward; tables present in Postgres.

**Decisions:** `WORKERS=2` (not 3): Colima VM has 8 GB, 3 workers plus 512Mi sandboxes would not fit a concurrency of ~20. Concurrency cap 4 on kind.

**Disk:** internal free fell 15 -> 9 GB over setup + phase 1 (cache folders `~/Library/Caches`, `~/.docker`, `~/.cache`, `~/.npm` unchanged). `/opt/homebrew` is 4.2 GB total; the new formulae (trivy 202M, colima/lima, kind, kubeconform and dependencies) account for part. I could not attribute the remainder (other system activity on the Mac is possible). Still above the 3 GB threshold; recheck next phase.

**Deferred:** ValidatingAdmissionPolicy (phase 4); dispatcher and runner (phase 2). Not yet verified: images are arm64 only.

**Next:** phase 2, one Python run end to end.

## Phase 2 — One Python run end to end (2026-11-16, ~1.5 h)

**Done:**
- Runner: `runner/Dockerfile.python` (python:3.12-slim pinned by digest, UID 10001) and `runner/ksc_run.py` wrapper: hard timeout (exit 124), JSON-lines framing so stdout/stderr are separable from the K8s log API, 1 MiB cap per stream (excess drained, `truncated` flagged).
- `dispatcher/podspec.py`: single fixed Job template (every hardening field asserted in `tests/unit/test_podspec.py`); user code only enters as an immutable ConfigMap owned by the Job.
- API: `POST /v1/runs` (202, `Idempotency-Key`, 409 on mismatched reuse, 422 validation), `GET /v1/runs/{id}` (private per key), hashed API keys, `python -m api.keys create`.
- Dispatcher (`dispatcher/core.py`): claim under a transaction-level advisory lock + `SKIP LOCKED` (never exceeds cap, tested with 8 threads), create Job+ConfigMap idempotently, observe pods, classify result (succeeded/failed/timed_out/oom_killed/infra_error), persist, then delete Job/ConfigMap. Terminal states are never overwritten. Chart deploys it.
- `make test` now starts a throwaway Postgres; `make e2e`.

**Verified how:** `make test` 40 passed; ruff + mypy clean; `make up` from the running cluster; `make e2e` 1 passed (`print('hello')` -> `succeeded`, stdout `hello\n`, node recorded) and the cleanup proof (Job, Pod, ConfigMap for the run gone within a bounded wait) passed; `make verify-cluster` still PASS; `sandboxes` namespace empty afterwards.

**Deviation from the handoff:** the "watch loop" is a 1 s poll of the pod list, not a Kubernetes watch stream. Simpler and robust for the chaos tests; revisit if latency matters.

**Deferred to phase 3:** cancel, reconcile (runs with no Job), sweeper, layer-2 timeout handling, per-key rate limit and concurrency cap (columns exist, not enforced), log streaming events, infra_error paths beyond pod-level.
**Not verified:** timeout, OOM and non-zero exit paths on the real cluster (unit-tested via fake only).

**Disk:** internal free 10 GB at end. The 15->9 GB drop was the Claude desktop app's own VM bundle (~9 GB) and a large log from an unrelated tool, not this project; project footprint on internal disk is ~350 MB of Homebrew CLIs.

## Phase 3 — Timeouts, OOM, exit codes, cancel, infra errors, sweeper, reconcile, idempotency (2026-11-16, ~1 h)

**Done:**
- Terminal states reachable and classified: `succeeded`, `failed` (exit code + stderr kept), `timed_out` (wrapper exit 124; job-deadline backstop maps to `timed_out/deadline_exceeded`), `oom_killed`, `cancelled`, `infra_error` (pod deleted from outside, job lost, image pull errors > 30 s, Pending > 120 s).
- `DELETE /v1/runs/{id}` cancels (queued/dispatching/running); terminal runs are a no-op; other keys get 404.
- Reconcile (every tick): live run without a Job is re-queued if it never started, else `infra_error/job_lost`; cluster objects whose run is missing or terminal are deleted. Objects without a run-id label are left to the sweeper.
- Sweeper (every 30 s, one replica via `pg_try_advisory_xact_lock`): deletes managed Job/Pod/ConfigMap older than min(timeout + 300 s, 600 s), unlabelled objects after 600 s; counts deletions in `dispatcher/metrics.py`.
- Idempotency verified end to end (same key, same run, one execution).

**Verified how:** `make test` 57 passed; ruff + mypy clean; `make e2e` 10 passed (hello, non-zero exit, exception, stdin, infinite loop times out within timeout + 8 s (not at the 33 s backstop), 2 GiB allocation -> `oom_killed`, cancel mid-run, Job deleted mid-run -> `infra_error`, idempotency, hand-made orphan Job + ConfigMap removed by the sweeper, confirmed via the "sweeper deleted stale object" log). Every terminal state is followed by the cleanup proof (Job, Pod, ConfigMap gone within 30 s). `make verify-cluster` still PASS; `sandboxes` ends empty.

**Found and fixed on the way:** a Job deleted mid-run was first classified `failed` (the pod exits 143 while being deleted); now `infra_error/pod_deleted` via `deletionTimestamp`. An old unit test encoded DeadlineExceeded as infra_error; corrected to timed_out.

**Deviations from the handoff (flagged):**
- Dispatcher RBAC gained `list` on configmaps and `delete` on pods (needed to find orphan ConfigMaps and delete orphan pods without a Job). Still namespaced, no secrets.
- Metrics are in-process counters (logged), not yet exported to Prometheus (phase 6).
- Reconcile runs every tick, not every 30 s, so cancel cleanup is prompt; cost is three list calls per second at this scale.

**Caveats:** the orphan e2e test temporarily sets sweeper env on the live dispatcher (restores afterwards; must not run concurrently with other e2e tests). Layer-2 (`activeDeadlineSeconds`) is only unit-tested via fakes, since the wrapper always fires first. Per-key rate limit and concurrency cap are still not enforced. Cancelling a queued run is unit-tested only.

**Next:** phase 4: admission policy (ValidatingAdmissionPolicy), attack suite, chaos tests, `ci.yml`, GitHub repo (public) and a real CI run.
