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

## Phase 4 — Admission policy, attack suite, chaos, CI (2026-11-16, ~2 h) — **Tier 1 complete**

**Done:** `ValidatingAdmissionPolicy` + binding on `sandboxes` (read-only rootfs, no token, cpu/memory/ephemeral-storage limits, only configMap/emptyDir volumes); attack suite (15 tests, `make attack`); chaos suite (2 tests, `make chaos`); `.github/workflows/ci.yml`; Postgres in the chart hardened (non-root, read-only rootfs, no capabilities) after trivy flagged it; public repo https://github.com/manynames3/k8s-sandbox-control-plane.

**Verified how (real CI run, ubuntu-24.04, 2026-11-16, ~8 min, run 43830478003):** lint-and-unit (60 unit), static-checks (gitleaks full history, kubeconform, cost traps, 5 MB file check, trivy config), cluster-tests (kind + Calico, trivy image scan CRITICAL, verify-cluster, 10 e2e, 15 attack, 2 chaos). All green. Same suites also pass locally.

**Found on the way:** kubelet eviction message for the emptyDir limit is `Usage of EmptyDir volume "tmp" exceeds the limit "64Mi"` (classifier only matched "ephemeral"); `KUBERNETES_*` service address env vars are always injected (an address, not a credential; network denial is tested separately); errno values in tests must be Linux's, not the Mac's; `gitleaks-action` failed on the runner ("stderr is not empty"), replaced by the pinned binary; `verify-cluster`'s own probe pod was (correctly) refused by the new admission policy and was made compliant.

**Not covered / honest limits:** tflint and Checkov are wired only when `infra/` exists (phase 8). Per-key rate limit and concurrency cap are still not enforced. kind runs runc: shared kernel (gVisor is the stretch goal in the AWS session).

## Phase 5a/5b — Node runtime, batch, log streaming, SSE (2026-11-16, in progress)

**Done:** `node:20-slim` runner (digest-pinned, same wrapper protocol in `runner/ksc_run.js`); per-language code file in the ConfigMap; `POST /v1/runs:batch` (1-20, atomic, idempotent per item); dispatcher persists output chunks as events while the run is `running` (unique `(run_id, line)` index makes this replica- and restart-safe); `GET /v1/runs/{id}/events` SSE with `Last-Event-ID` resume; loop interval 0.5 s.
**Found:** the Python wrapper used `read(4096)` on a buffered pipe, so output reached the log only at exit; fixed with `read1`. The SSE e2e test (logs arrive >4 s before an 8 s run ends) caught it.
**Verified:** 69 unit, e2e incl. Node hello/exit/stdin/timeout/OOM, batch of 20 (cap never exceeded at any sample, both languages, ordered output), SSE (3/3 runs).

## Phase 5c/5d — Web UI (live mode), cluster/list endpoints, MCP server (2026-11-16)

**Done:**
- `GET /v1/runs` (status/language filters, keyset cursor pagination, no code/output in the list), `GET /v1/cluster` (queue and slot counts live from the DB; node view from a snapshot the dispatcher writes, because the API has no Kubernetes access by design; worker names come from `make up` via Helm `knownNodes`), CORS limited to the local UI origins.
- `web/`: Vite + React + TypeScript, 155 kB JS (50 kB gzipped). Editor, language, timeout, Run/Cancel, live log stream (fetch-based SSE reader so the API key can go in a header), status/exit/reason/duration/node, cluster panel, recent-runs table, eight "Try an attack" buttons with the healthy outcome shown. All data goes through a `DataSource` interface so replay mode (phase 7) reuses every screen.
- MCP server `mcp/ksc_mcp_server.py` (MCP SDK 2.x, stdio): `run_code`, `submit_batch`, `get_run`, `cancel_run`; output returned to the agent is capped at 20,000 characters per stream and flagged.
- `docs/agent-transcript.md`: a real one-shot `claude -p` session (haiku-5-5, only this MCP server attached) against the local cluster: primes in Python, a Node TCP connect to example.com (timed out), a write to /etc/hostname (errno 30).

**Verified how:** 85 unit tests (list/pagination/filters/cluster/CORS/MCP tools with a faked API); e2e: MCP spawned over stdio exactly as an agent would (list tools, run Python and Node, failing run, batch, cancel) + cleanup proof; web typecheck, 3 vitest tests, production build; UI driven in a real browser against the live cluster (run streamed line by line, cluster panel showed the busy node and slot, network attack ran and every outbound attempt printed `closed:`).

**Limits:** no browser automation suite for the UI (checked manually in a browser, not in CI); the UI has no screenshot tests; node view for an unknown node appears only once it hosts a sandbox.

## Phase 6a-c — Metrics, KEDA, monitoring (2026-11-16, in progress)

**Done:** `/metrics` on the API (counters, gauges and the five latency histograms computed from the runs table at scrape time, `ksc_` prefix, no code or output) and on the dispatcher `:9100` (sweeper deletions, reconcile corrections, k8s API errors); KEDA (chart 2.21.0) scaling dispatcher replicas on queued-run count (PostgreSQL scaler, 1-3 replicas, one per 4 queued); optional kube-prometheus-stack (`make monitoring`) with ServiceMonitors, 3 alerts (queue wait p95, sandbox overdue/sweeper deletion, infra_error ratio) and a provisioned 12-panel Grafana dashboard (generated by `scripts/gen_dashboard.py`).
**Verified how:** 88 unit tests; e2e `test_keda.py`: 40 queued runs scaled the dispatcher out to >=2 replicas, the cap held at every sample with several dispatchers, each run's Job was created exactly once, replicas returned to 1; live Prometheus: both targets `up`, 3 rules `ok`, queries return data.
**Found:** KEDA's `cooldownPeriod` only governs scaling to zero; scaling N->1 follows the HPA's 5-minute stabilization window, so `scaleDownStabilizationSeconds: 30` is set explicitly.
**CI incident:** run 43832947686 failed (`Attack suite`: 11 errors at setup). Cause: I added an `api_key` fixture to the e2e conftest for the MCP test but the attack/chaos conftests imported only `api_url` and `client`. I had not rerun those suites locally after the change. Fixed; attack 15/15 and chaos 2/2 pass locally.
