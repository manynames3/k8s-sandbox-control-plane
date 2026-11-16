# Cost model

**Idle cost target: under $0.10 per month, all-in.** Nothing runs in AWS between sessions.

## What exists between sessions

| Thing | Where | Idle cost |
|---|---|---|
| Local development (kind on a Mac) | the owner's machine | $0 |
| CI (kind on a GitHub runner) | GitHub-hosted, public repository | $0 |
| Public replay site | Cloudflare static assets (free plan) | $0 |
| Terraform state bucket (a few KB) | S3, `infra/bootstrap` | cents per month at most |
| IAM role, OIDC provider, one budget | AWS | $0 (the first budgets are free) |
| Run history | Neon free plan, suspends when idle | $0 |
| Container images | GHCR (free for public images) | $0 |

No NAT gateway, no load balancer, no RDS, no always-on cluster, no KMS key, no Elastic IP. These are enforced, not
just avoided: `scripts/check-cost-traps.sh` runs in CI and fails the build (see "Traps" below).

## Cost of one EKS session

Estimate printed by the session workflow (`scripts/cost_estimate.py`), us-east-1, on-demand. **Prices are constants in
that script, not looked up live.** Verify them with the AWS Pricing Calculator before the first session and record the
result here.

| Item | Price used | 2-hour session |
|---|---|---|
| EKS control plane (standard support) | $0.10 per cluster-hour | $0.20 |
| 3 x `t3.medium` | $0.0416 per instance-hour | $0.25 |
| 3 public IPv4 addresses | $0.005 per address-hour | $0.03 |
| 3 x 20 GB gp3 | $0.08 per GB-month | $0.01 |
| **Total** | | **about $0.49** |

A four-hour session (the maximum) is about $1. Self-imposed ceiling for the whole project: **$25 of the $100 credit**.

Provenance of the prices: the `t3.medium` price was cross-checked against third-party price lists on 2026-11-16. The EKS
and public-IPv4 prices are from the project handoff and the author's knowledge and are **unverified**. Data transfer is
small and not included.

**Measured session cost:** not yet measured. After the first session, read the actual figure from Cost Explorer (filter on
the tag `project=ksc`, enable the tag as a cost-allocation tag first) and replace this paragraph.

## Traps designed out, each with a guard

| Trap | Why it matters | Guard |
|---|---|---|
| **Extended support** | A cluster on a version past standard support bills $0.60/hour, six times the normal rate. | Terraform pins 1.36; `scripts/check-eks-version.py` fails CI if it is within 60 days of leaving standard support (end date from `infra/eks/version-support.json`); the session workflow re-checks the live EKS API (`aws eks describe-cluster-versions`) and refuses to continue unless the status is `STANDARD_SUPPORT`. |
| **NAT gateway** | About $32/month each, plus data processing. | None exists. Nodes are in public subnets with public IPs and security groups that allow no inbound traffic. CI fails if `aws_nat_gateway` appears or `enable_nat_gateway` is not `false`. |
| **Load balancers** | Hourly charge each. | No `Service` of type `LoadBalancer`, no Ingress. CI fails the build if the rendered chart contains either. The API is reached through `kubectl port-forward`. |
| **EKS Auto Mode / Provisioned Control Plane** | Add per-instance and capacity fees. | Auto Mode is explicitly off (`compute_config.enabled = false`), checked in CI. |
| **KMS key, control-plane logs, IRSA provider** | Small recurring charges that add up. | Off, checked in CI. |
| **Orphans** | A forgotten resource bills until someone notices. | Everything is tagged `project=ksc` and `expires-at`. After destroy the session workflow runs two independent checks (the Resource Groups Tagging API, and direct EKS/EC2/IAM queries that need no tags) and fails loudly, opening an issue, if anything remains. If `terraform destroy` fails it falls back to `scripts/aws_force_cleanup.sh`. The `aws-reaper` workflow runs every hour and destroys anything past its expiry or with no expiry tag. |
| **The reaper itself stops running** | GitHub pauses scheduled workflows in repositories with no activity for 60 days. | The budget alerts ($5, $15 and $25 of actual spend, plus a forecast alert) are the final backstop. They notify; they do not stop spending. |
| **Credit eligibility** | The credit may not cover EKS or EC2 on every account plan. | Owner checklist item 1 in `docs/AWS.md`. |

## Neon (database during sessions)

Neon replaces RDS: RDS would cost money while idle and take minutes to create; Neon suspends when idle and keeps run
history across ephemeral clusters. The dispatcher only polls while a cluster exists, so Neon is asleep the rest of the time.

Free plan limits as read on **2026-11-16** from Neon's documentation (**sources disagreed with each other, and Neon has
changed these numbers before; check the live plans page before relying on them**): 100 CU-hours of compute per project
per month, 1 GB of storage per project (20 GB across all projects), 5 GB of public network transfer per project per month.
Compute suspends when the monthly allowance is used up.

To stay inside the storage limit the dispatcher prunes output chunks of finished runs older than `logRetentionDays`
(default 7) once an hour (`prune_old_logs`, unit-tested). Run records, state events and each run's final stdout/stderr are
kept.

## Where the money would go next

Karpenter for node autoscaling, private nodes with NAT or VPC endpoints, a multi-AZ database and per-tenant isolation each
add recurring cost; they are described in `docs/AWS.md` as what a real deployment would change, and are not built.
