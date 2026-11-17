# AWS: the ephemeral EKS session

**Status: written and validated, never run.** Everything in `infra/` and the AWS workflows passes `terraform fmt`,
`terraform validate`, tflint, Checkov, trivy, actionlint and the cost-trap checks, but no `terraform plan` or `apply` has
been executed: that needs an AWS account, and creating anything that bills is the owner's action. Treat every claim
below about how the cluster behaves on EKS as **untested until the first session**.

## What a session proves

A session creates a real EKS cluster for 1 to 4 hours and runs the same suites that run on kind, plus the things kind
cannot show:

1. **NetworkPolicy is enforced on this cluster.** Run first; if it fails, the session stops (nothing else would mean
   anything). On EKS enforcement comes from the VPC CNI network policy agent, not Calico.
2. e2e, attack suite, cleanup proof and chaos tests, against **Neon** (a transaction-mode pooler), proving the "must work
   through a pooler" decision.
3. **Instance metadata is unreachable from pods** (IMDSv2 required, hop limit 1): `tests/attack/test_eks_only.py` runs a
   probe in a namespace with no NetworkPolicy, with a `hostNetwork` pod as the positive control.
4. The benchmark on real `t3.medium` nodes, with images pulled from GHCR (also the only place the pulled versus
   pre-pulled cold start can be measured).
5. Optional, best effort: gVisor under the sandbox node group (`deploy/eks/gvisor.sh`, **never run**). If it fails the
   workflow records where and the rest of the session is unaffected.
6. `terraform destroy` in an `if: always()` step, then proof that nothing remains (see "How teardown is made solid").

## One-time setup (owner checklist)

1. **Confirm the credit.** In the Billing console, check that your credit covers EKS and EC2 and that the account plan
   permits them. Nothing below should be done before this.
2. **Apply the bootstrap once, from your own machine.** It creates the state bucket, the GitHub OIDC provider, the CI
   role and the budget alerts.
   ```bash
   cd infra/bootstrap
   terraform init
   terraform plan  -var owner_email=you@example.com      # read the plan
   terraform apply -var owner_email=you@example.com
   ```
   If the account already has the GitHub OIDC provider, add `-var create_oidc_provider=false`. If the repository uses GitHub's
   immutable OIDC subject (owner and repository ids inside the `sub` claim; check with
   `gh api repos/OWNER/NAME/actions/oidc/customization/sub`), also pass
   `-var "github_sub_prefix=$(gh api repos/OWNER/NAME/actions/oidc/customization/sub -q .sub_claim_prefix)"`, otherwise every
   workflow fails with "Not authorized to perform sts:AssumeRoleWithWebIdentity" (this happened on the first reaper run). Note the outputs
   `state_bucket` and `ci_role_arn`. Bootstrap state is local and git-ignored: keep it.
3. **GitHub repository variables:** `AWS_ROLE_ARN` = `ci_role_arn`, `TF_STATE_BUCKET` = `state_bucket`.
4. **GitHub repository secrets (Neon):** create a Neon project, then add
   `NEON_DATABASE_URL` (the **pooled** connection string, rewritten as
   `postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require`) and `NEON_DATABASE_URL_DIRECT` (the **direct**
   connection string, `postgresql://...?sslmode=require`; KEDA uses it).
5. **GitHub Environment `eks-session`:** add yourself as a required reviewer and restrict deployment branches to `main`.
   The CI role trusts only this environment (and workflows on `main`).
6. **Images:** run the `images` workflow once, then make the three GHCR packages public (Packages > settings >
   visibility), otherwise EKS nodes cannot pull them. The session workflow checks that the images for the commit exist
   and are pullable before spending anything.
7. **Plan (read-only):** `TF_STATE_BUCKET=<state_bucket> make aws-plan` shows what would be created. Nothing is created.
8. **Start a session:** Actions > eks-session > Run workflow, choose the lifetime, type `CREATE EKS SESSION`, approve the
   deployment. Expect about $0.25 per hour.
9. **Afterwards:** read the real cost from Cost Explorer and record it in `docs/COST.md`.

## How teardown is made solid

Layers, in order. All are written and unit-tested against a stub `aws` (`tests/unit/test_teardown.py`); **none has run against AWS**.

1. **Terraform destroy**, always, with fresh credentials, three attempts, a 5-minute lock wait and on the last attempt `-lock=false`
   (a killed apply can leave the state lock behind; the concurrency group means nothing else legitimately holds it).
2. **Direct deletion** (`scripts/aws_force_cleanup.sh --yes`) if Terraform could not finish: EKS node groups and cluster, instances,
   Elastic IPs, VPC networking and launch templates, only what is tagged `project=ksc` (a dry run without `--yes`). Then Terraform
   runs again to remove the IAM roles and reconcile the state.
3. **Two independent checks** after the destroy: the tag index (lags, misses some resource types) and direct service queries
   (`aws_orphans.sh deep`: clusters, instances, VPCs, network interfaces, volumes, addresses, NAT gateways, security groups, launch
   templates, the three IAM roles). Any hit fails the run and opens an issue.
4. **The reaper**, every hour, never concurrent with a session: destroys if anything is past its `expires-at`, has **no** `expires-at`,
   or shows up in the direct check, with the same direct-deletion fallback.
5. **Bounded steps**: every long step has a timeout and the job allows an hour after the last one, so a hang cannot starve the teardown.
6. **Budget alerts** ($5, $15, $25) notify you if everything above fails. If GitHub pauses scheduled workflows (60 days without
   activity), only this layer remains.

Known gaps: the direct deletion is the least-tested code here (the stub proves order and scoping, not that AWS accepts each call);
a failure inside AWS itself is out of reach; resources created outside Terraform without the `project=ksc` tag are invisible to every
layer except the three IAM role names and the cluster-name checks.

## What each IAM permission is for

The CI role (`infra/bootstrap/main.tf`) is scoped to what the EKS stack needs:

| Permission | Scope | Why |
|---|---|---|
| S3 read/write | the state bucket's `eks/` prefix | Terraform state and its lock file |
| `ec2:*` | region-limited | VPC, subnets, security groups, launch templates, instances |
| `eks:*` | region-limited | the cluster, add-ons, node groups, access entries |
| IAM role management (create, delete, tag, read) | only names `ksc-session-*` | the cluster and node roles (names are fixed in `infra/eks` so this scoping works). `iam:PutRolePolicy` and `iam:UpdateAssumeRolePolicy` are **deliberately not granted** (see below) |
| `iam:AttachRolePolicy` | only six named AWS managed policies | EKS cluster, VPC resource controller, worker node, ECR read, CNI |
| `iam:PassRole` | only those roles, only to EKS and EC2 | no escalation to other services |
| Service-linked roles | EKS, node group, autoscaling | created on first use |
| `ssm:GetParameter` | `/aws/service/eks/*` | node AMI lookup |
| read-only helpers | `*` | `autoscaling:Describe*`, tag lookup for the orphan check |

**Escalation path that was closed:** the first version granted `iam:PutRolePolicy` and `iam:UpdateAssumeRolePolicy`, which would have let code running in the session create a `ksc-session-*` role with an `Action: *` inline policy and a trust policy naming the CI role, then assume it (an escalation to account admin). Found by the independent review. Without them a role created here can carry only the six managed policies above. If the first apply fails with `AccessDenied` on one of these, decide consciously before adding it back (a permissions boundary is the safer way).

**Honest breadth:** `ec2:*` and `eks:*` are wide even though they are limited to one region. Resource-level scoping is
not practical for the many resources the modules create. The role can only be assumed by this repository's session
workflow (through an environment that needs your approval) and by workflows running on `main`; a pull request or a fork
cannot assume it. If `main` is compromised, so is the role.

## What a real deployment would change

| Here (and why) | A real deployment |
|---|---|
| Nodes in public subnets with public IPs, no NAT (a NAT gateway is about $32/month) | Private nodes with NAT or VPC endpoints |
| Public API endpoint limited to one runner address | Fully private endpoint, access through a VPN or an in-VPC runner |
| No KMS envelope encryption of secrets, SSE-S3 on state | KMS everywhere |
| Fixed two sandbox nodes | Karpenter scaling nodes on queue depth (described here, **not built**) |
| Neon free plan | A multi-AZ managed database |
| One shared kernel (runc); optional gVisor attempt | gVisor or Firecracker/Kata by default |
| Single tenant | Per-tenant namespaces, quotas and network isolation |
| Public subnets and open egress from nodes | Egress filtered at the VPC |

## Failure modes to expect on the first run

I could not test any of the following, so they are the likely places for trouble:

- Terraform module inputs are validated by `terraform validate`, but not by an actual apply (quota limits, AZ capacity,
  a missing service-linked role).
- Node `t3.medium` capacity: 3 nodes with 4 GB each is tight for KEDA, the API and the dispatcher on the system node.
- The VPC CNI network policy agent needs the add-on version and `enableNetworkPolicy` to take effect; the first
  verification step exists to catch exactly this.
- GHCR packages are private until made public.
- Neon cold starts: the connect timeout is 10 s on EKS (3 s on kind) for this reason.
- The Neon pooled endpoint with KEDA: KEDA uses the direct endpoint.
