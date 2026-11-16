# ADR 0011: Images on GHCR, multi-arch

Status: accepted (2026-11-16)

**Context.** The development Mac is arm64 and the EKS nodes are amd64.

**Decision.** Build `linux/amd64,linux/arm64` with buildx and push to GHCR (free for public images), pinning base images by
digest. No ECR.

**Consequences.** Packages are private until made public (owner checklist). The arm64 build runs under emulation in CI and is
slow. Local development uses images loaded into kind.
