# ADR 0010: GitHub OIDC to AWS, no long-lived keys

Status: accepted (2026-11-16)

**Context.** CI needs AWS access without storing keys in GitHub.

**Decision.** The workflow assumes an IAM role through GitHub's OIDC provider. The trust policy allows only this repository's
`eks-session` environment (which needs the owner's approval) and workflows on `main`. The permission policy is scoped to what
the stack needs: IAM only for `ksc-session-*` names and a fixed list of managed policies, region-limited EC2/EKS.

**Consequences.** No secret to leak or rotate. `ec2:*` and `eks:*` remain broad; a compromise of `main` is a compromise of the
role. Whether the scoped policy is *sufficient* is unknown until the first apply.
