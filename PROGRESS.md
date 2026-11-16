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
