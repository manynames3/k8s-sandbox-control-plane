# PROGRESS

## Phase 0 — Preflight (2026-11-16, started)

**Done:** repo scaffold (layout dirs, `git init`, `.gitignore`), `docs/HANDOFF.md`, `CLAUDE.md`, 13 ADR stubs, `Makefile` with `doctor`/`disk`/`clean`/`nuke`, `scripts/doctor.sh`.

**Environment fix (2026-11-16):** `/Volumes/CRUCIAL 525GB` is a read-only macOS system volume; the writable half is `/Volumes/CRUCIAL 525GB - Data` (holds `Users/Codex`). `scripts/mac-setup.sh` (adapted from the owner's script) puts `cx` at `.../Data/Users/Codex/cx`, addressed via the space-free symlink `/Users/Shared/ksc-cx` (`$CX`), installed colima/kind/trivy/kubeconform, started Colima (VM on the drive), switched docker context to colima.
**Verified:** `make doctor` PASS with `source /Users/Shared/ksc-cx/env.sh` (cgroup v2, colima context, all tools, drive 168 GB / internal 13 GB free). Internal disk spots unchanged (Caches 3.3G, .cache 2.9G, .npm 2.0G; pre-existing).
**Caveats:** drive does not report as SSD (USB, "Info not available"); watch kind stability/perf. Colima VM: 4 CPU / 8 GB / 60 GB sparse.

**Next:** owner checkpoint A (repo name, public, worker count), commit phase 0, then phase 1.
