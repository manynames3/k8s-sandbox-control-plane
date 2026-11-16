# k8s-sandbox-control-plane (prefix `ksc`)

Small, self-hostable reference implementation of the isolation and cleanup layers for running untrusted code in short-lived Kubernetes sandboxes. Source of truth: `docs/HANDOFF.md`.

**Read `PROGRESS.md` first, run `make doctor`, update `PROGRESS.md` last.**

## Hard rules
- Never run anything that creates AWS resources (terraform apply, eksctl, AWS create calls). Only the owner triggers `eks-session`.
- Never write large files to the internal disk; everything heavy lives under `$CX` (external drive). No host bind mounts into the VM.
- No secrets in git. Never fake a green check; record failures under Known limitations.
- Every README number comes from an executed run and a results file, with provenance.
- The execution API is never exposed to the internet.
- Scope: Tier 1 = phases 0-4. Stop when green in real CI and write the final report.

## Tests
`make test` (unit), `make e2e`, `make attack`, `make chaos` (added as phases land). `make doctor` for the environment.
