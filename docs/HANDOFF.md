# k8s-sandbox-control-plane — build handoff (v4)

**For:** the build agent (Claude Opus 5.5 in Claude Code, on the owner's Mac).
**From:** planning session, 2026-11-15. This file supersedes the earlier `the earlier prompt file`.
**Naming:** the repo and project are called `k8s-sandbox-control-plane`. Short identifier prefix `ksc` is used for namespaces, labels, the Helm chart and AWS tags.
**Save as:** `docs/HANDOFF.md` in the new repo. It is the source of truth for scope and decisions. If you disagree with a decision, write an ADR proposing the change and ask; do not silently diverge.

---

## 1. What we are building and why

**k8s-sandbox-control-plane** is a small platform where an AI agent (via MCP or REST) or a person (via a web UI) submits untrusted code, and it runs in an isolated, short-lived Kubernetes sandbox that is provably cleaned up afterwards.

It is an independent portfolio project. The owner already has strong public evidence of AWS + Terraform delivery (ECS/RDS), serverless, and AI-assisted ops tooling. What the portfolio lacks, and what this project must supply:

1. **Kubernetes that actually runs** — a controller, admission policy, enforced NetworkPolicy, autoscaling.
2. **Adversarial and chaos testing** — proof the system holds when attacked or killed mid-flight, not only that the happy path works.
3. **Operability** — Prometheus/Grafana, an SLO, alerts, a runbook.
4. **Cost discipline** — an architecture that idles at about $0 and an ephemeral AWS environment with an enforced lifetime.

Feature count is not the goal. A reviewer should be able to verify every claim in the README from a test, a CI run, or a results file.

## 1a. Positioning and scope tiers

**Positioning line (use in the README, near the top):** "A small, self-hostable reference implementation of the isolation and cleanup layers that hosted sandbox providers abstract away. Not a replacement for them."

**Scope is tiered, and the owner decides when to move up.**
- **Tier 1 = phases 0–4.** Cluster, one Python run end to end, timeouts and cleanup, admission policy, the attack suite and chaos tests, and CI passing. This alone fills the portfolio gap (Kubernetes with adversarial proof). **When Tier 1 is green in a real CI run, stop, write the report in section 15, and wait.**
- **Tier 2 = phases 5–9.** Node runtime, batch, SSE, UI, MCP server, KEDA, observability, benchmark, public replay site, AWS session. Start these only if the owner says to continue.
- **Time box:** the owner intends to spend about two weeks of part-time work on Tier 1. Record the date and effort at the end of every phase in `PROGRESS.md`. If Tier 1 is not green after about two weeks, say so plainly and recommend shipping what works with honest limitations rather than adding scope.
- Owner checkpoints B, C and D (section 12) apply to Tier 2 only. Tier 1 needs only checkpoint A.

---

## 2. Hard rules

- **Spending money is the owner's action, never yours.** You write and validate all AWS infrastructure code, but you never run `terraform apply`, `eksctl create`, or any AWS create call. The only path to spend is the owner manually triggering the `eks-session` workflow (section 9). The same applies to anything else that could bill.
- **Idle cost target: under $0.10 per month, all-in.** Nothing may be left running in AWS between sessions. No NAT gateway, no load balancer, no RDS, no always-on cluster, ever.
- **Honesty.** Demo code and data are fictional. No claims of SOC 2, uptime, customers or scale. Every number in the README comes from a run you executed, generated from a results file, labelled with: date, environment (kind on Mac / GitHub runner / EKS), sample size, concurrency, machine or instance type, Kubernetes version, container runtime. Unmeasured means unwritten.
- **Never fake a green check.** If something does not work, record it under "Known limitations". Do not stub, skip silently, or weaken a test to pass.
- **This is a remote-code-execution service by design.** The execution API is never exposed to the internet: localhost on kind, `kubectl port-forward` from the CI runner on EKS. The public website is a static replay of recorded runs and cannot execute anything.
- **No secrets in git.** Neon URL, Cloudflare token, API keys live in `.env` (git-ignored), Kubernetes Secrets, and GitHub Actions secrets. Run `gitleaks` in CI.
- **Original work.** Own name, own UI, own copy. Do not imitate any existing sandbox product.
- **Working style.** Work phase by phase without asking for approval, except at the four owner checkpoints in section 12. Commit at the end of each phase. Keep `PROGRESS.md` current (done / verified how / deferred / next). Do not start a phase until the previous phase's "Done when" passes. If you run short, cut from the bottom of the phase list; never cut tests or cleanup.

## 3. Environments and what each costs

| Environment | Purpose | Cluster | Database | Idle cost |
|---|---|---|---|---|
| `local` | Day-to-day development, full test suites | kind on the Mac (1 control-plane + 3 workers) | Postgres container in-cluster | $0 |
| `ci` | Every push: lint, unit, e2e, attack, cleanup, chaos | kind on a GitHub-hosted runner | Postgres service container | $0 (public repo) |
| `eks-session` | Proof on real AWS: run suites + benchmark, then destroy | Ephemeral EKS, lives 1–3 hours | Neon (free plan) | $0 between sessions |
| `showcase` | Public site a hiring manager can open any time | none | none (static snapshot) | $0 (Cloudflare free plan) |

Persistent AWS footprint between sessions: one S3 bucket for Terraform state and one IAM role + OIDC provider. Cents per month at most.

**Per-session AWS estimate (verify with the AWS Pricing Calculator before the first session and record the result in `docs/COST.md`):** EKS control plane is $0.10 per cluster-hour on a Kubernetes version in standard support. Three `t3.medium` nodes plus small gp3 volumes and public IPv4 addresses bring the total to very roughly $0.25–0.30 per hour, so a two-hour session should cost well under $1. Self-imposed ceiling for the whole project: **$25 of the $100 credit**.

Cost traps to design out, each with a guard:

- **Extended support.** A cluster created on a Kubernetes version past standard support bills at $0.60 per hour, six times the normal rate. Terraform must pin a version currently in standard support; a CI check fails if the pinned version is within 60 days of leaving it.
- **NAT gateway.** None. Nodes sit in public subnets with public IPs and security groups that allow no inbound traffic.
- **Load balancers.** None. No `Service` of type `LoadBalancer`, no Ingress controller. A `kubeconform`/policy check in CI fails the build if one appears in the chart.
- **EKS Auto Mode and Provisioned Control Plane.** Off. Both add fees.
- **Orphans.** Everything is tagged `project=ksc`. After destroy, a script queries the Resource Groups Tagging API and fails loudly if anything remains (section 9).
- **Credit eligibility.** Before the first session, the owner confirms in the Billing console that the credit covers EKS and EC2 and that the account plan permits them. This is on the owner checklist; you remind them.

## 4. Architecture decisions (write each as a short ADR in `docs/adr/`)

1. **Postgres is both database and queue.** A run row is its own queue entry, so there is no dual-write and no lost job between "save" and "enqueue". Workers claim with `SELECT … FOR UPDATE SKIP LOCKED`. No Redis.
2. **Must work through a transaction-mode connection pooler.** Neon's pooled endpoint is PgBouncer in transaction mode, so: no `LISTEN/NOTIFY`, no session-level advisory locks, no reliance on session state. Use polling and `pg_try_advisory_xact_lock`. The same code then runs unchanged on the in-cluster Postgres and on Neon.
3. **Neon instead of RDS for cloud sessions.** RDS would cost money while idle and take minutes to create. Neon suspends when idle and keeps run history alive across ephemeral clusters. The dispatcher only polls while a cluster exists, so Neon is asleep the rest of the time. Add a retention job that prunes old log chunks to stay inside the free plan's storage; check the current free-plan limits and record them in `docs/COST.md`.
4. **The sandbox never talks to the platform.** No network, no credentials, no service-account token. The dispatcher collects results from outside: exit code and termination reason from container status, stdout/stderr from the Kubernetes log API with a byte limit.
5. **Security is enforced on the pod spec by the cluster, not by scanning user code.** The dispatcher builds every pod from a fixed template the user cannot influence. Namespace-level admission rules make the guarantees hold even if the dispatcher has a bug. No "does this code look malicious" heuristics. Input validation is only: language allowlist, code size cap, timeout range.
6. **The dispatcher is a reconciling controller.** The database holds intent, the cluster holds reality, the dispatcher converges them. Job names are deterministic (`run-<run_id>`), so creation is idempotent and a crash mid-dispatch cannot duplicate a run.
7. **Spread with `topologySpreadConstraints`** (maxSkew 1 on hostname, `ScheduleAnyway`) plus resource requests. Hard anti-affinity would cap concurrency at node count.
8. **One static UI, two data sources.** The UI is a static single-page app. In *live* mode (local only) it calls the API on localhost. In *replay* mode (the public site) it plays back recorded runs from a JSON snapshot bundled at build time. No server component, no database credentials on the public site, nothing to keep warm.
9. **Ephemeral EKS with an enforced lifetime instead of an always-on cluster.** The session workflow creates, tests, measures and destroys in one run, with destroy in an `always()` step and an independent reaper as backstop.
10. **GitHub OIDC to AWS, no long-lived keys.** The workflow assumes a role scoped to this repo and branch.
11. **Images on GHCR, multi-arch.** The Mac is arm64 and the EKS nodes are amd64, so build `linux/amd64,linux/arm64` with buildx. GHCR is free for public images; no ECR.
12. **Honest scaling story.** On kind, nodes are fixed, so what is demonstrated is queue backpressure, a concurrency cap, and KEDA scaling dispatcher replicas on queue depth. Node autoscaling (Karpenter) is described in `docs/AWS.md` as the next step and is not built.

13. **Why not build on `kubernetes-sigs/agent-sandbox`?** Record this as `docs/adr/0013-why-not-agent-sandbox.md`. That project provides a `Sandbox` CRD and controller (plus templates and warm pools) for long-lived, stateful, singleton workloads such as an agent's persistent workspace, and delegates isolation to a `RuntimeClass` such as gVisor or Kata. This project runs one-shot jobs: submit, run, collect result, delete. The queue, concurrency cap, timeout layers, result capture and cleanup reconciliation are not part of that project's scope as far as its README states. Decision: do not depend on it in v1, because (1) the workload shape differs, (2) the surrounding platform would still have to be built, and (3) the learning and proof goal is to build and attack the isolation and cleanup layers directly. Consequences: more code to maintain, and a weaker isolation default than a gVisor-backed approach unless the gVisor stretch works. Revisit if a workload needs long-lived state or warm pools; the dispatcher could then create `SandboxClaim`s instead of Jobs. **Before writing the ADR, re-read that project's current README and docs and correct anything above that has changed.**

## 5. Tech stack

| Layer | Choice |
|---|---|
| Control plane | Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, `psycopg` 3, official `kubernetes` client, `uv`, `ruff`, `mypy`, `pytest` |
| Database / queue | Postgres 16 (container locally and in CI; Neon for EKS sessions) |
| Local cluster | kind, Kubernetes version pinned, default CNI disabled and Calico installed so NetworkPolicy enforcement is real; kubelet `podPidsLimit` set |
| Cloud cluster | EKS via Terraform (`terraform-aws-modules/vpc` and `/eks`, versions pinned), managed node groups, VPC CNI with network policy enabled |
| Autoscaling | KEDA, PostgreSQL scaler |
| Sandbox images | `python:3.12-slim` and `node:20-slim` based, pinned by digest, non-root, small entrypoint wrapper, multi-arch, on GHCR |
| Packaging | One Helm chart, `charts/ksc`, with `values-kind.yaml` and `values-eks.yaml` |
| Web UI | Vite + React + TypeScript, static build, deployed to Cloudflare (Pages or Workers static assets, whichever `wrangler` currently recommends) |
| Agent interface | MCP server in Python over stdio |
| Observability | Prometheus metrics, JSON logs with `run_id`, kube-prometheus-stack via Helm, provisioned Grafana dashboard, `PrometheusRule` alerts |
| CI/CD | GitHub Actions: `ci.yml`, `images.yml`, `showcase.yml`, `eks-session.yml`, `aws-reaper.yml` |
| IaC checks | `terraform fmt/validate`, `tflint`, Checkov, `kubeconform`, `trivy`, `gitleaks` |
| Task runner | `Makefile`: `up`, `down`, `demo`, `test`, `e2e`, `attack`, `chaos`, `bench`, `snapshot`, `aws-plan` |

## 6. Repository layout

```
k8s-sandbox-control-plane/
  api/                FastAPI app, models, state machine, migrations
  dispatcher/         claim loop, watch loop, reconcile, sweeper, pod spec builder
  runner/             sandbox images and entrypoint wrapper
  mcp/                MCP server
  web/                static UI (live + replay modes)
  charts/ksc/ Helm chart
  deploy/kind/        kind config, CNI install, bootstrap scripts
  infra/bootstrap/    Terraform: state bucket, OIDC provider, CI role, budgets
  infra/eks/          Terraform: VPC, EKS, node groups
  tests/unit/  tests/e2e/  tests/attack/  tests/chaos/
  bench/              load script, results/<date>-<env>.json
  docs/               HANDOFF.md, adr/, THREAT_MODEL.md, RUNBOOK.md, AWS.md, COST.md, demo.md
  CLAUDE.md  PROGRESS.md  README.md  Makefile
```

`CLAUDE.md` is short: project one-liner, the hard rules from section 2, how to run each test suite, and "read `PROGRESS.md` first, update it last".

## 7. Core design

### Trust boundaries

- `ksc-system` namespace: API, dispatcher, (local) Postgres. Trusted. On EKS it runs on the system node group and is allowed egress to Neon only.
- `sandboxes` namespace: one Job per run and nothing else. Untrusted. On EKS it runs on a dedicated tainted node group.

Dispatcher RBAC is a namespaced `Role` in `sandboxes`: jobs (create, get, list, watch, delete), pods (get, list, watch), pods/log (get), configmaps (create, get, delete), events (list). Nothing cluster-wide, no secrets.

### Sandbox pod template (every line is asserted by a unit test and at least one attack test)

- `runAsNonRoot`, fixed non-zero UID/GID, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`, `readOnlyRootFilesystem: true`
- `automountServiceAccountToken: false`, `enableServiceLinks: false`, a service account with zero RBAC
- No `hostNetwork`, `hostPID`, `hostIPC`, `hostPath`, host ports
- Only writable path is an `emptyDir` at `/tmp` with a `sizeLimit`; `ephemeral-storage` limits set
- Requests equal limits (Guaranteed QoS): default 500m CPU / 512Mi
- User code arrives as a read-only ConfigMap mount owned by the Job (`ownerReferences`), max 64 KiB
- Job: `backoffLimit: 0`, `restartPolicy: Never`, `ttlSecondsAfterFinished: 120`, `activeDeadlineSeconds: timeout + 30`, `terminationGracePeriodSeconds: 5`
- Timeout layers: (1) the entrypoint wrapper kills the process at `timeout_seconds` and exits with a distinct code, so a timeout is distinguishable from an OOM kill; (2) `activeDeadlineSeconds`; (3) the sweeper
- Labels: `app.kubernetes.io/managed-by=ksc`, `ksc.dev/run-id=<id>`

### Namespace guardrails on `sandboxes`

- Pod Security Admission `enforce: restricted`
- `ValidatingAdmissionPolicy` requiring read-only root filesystem, no service-account token, resource limits on every container, and only `configMap` / `emptyDir` volumes
- `NetworkPolicy` default-deny for ingress and egress, DNS included. Optional egress allowlist via Helm values, empty by default; when used, `169.254.0.0/16` and cluster CIDRs stay blocked
- `ResourceQuota` (pod count equals the concurrency cap) and `LimitRange`
- On EKS additionally: IMDSv2 required with hop limit 1 on the nodes, so pods cannot reach instance credentials

### API (`Authorization: Bearer <key>`; keys stored hashed; per-key rate limit and concurrency cap)

- `POST /v1/runs` — `{language: "python3.12"|"node20", code, timeout_seconds (default 30, 1–120), stdin?}`; honours `Idempotency-Key`; returns `202`
- `POST /v1/runs:batch` — up to 20 runs
- `GET /v1/runs/{id}` — status, timestamps, exit code, termination reason, stdout/stderr with `truncated` flag, node
- `GET /v1/runs/{id}/events` — Server-Sent Events of state changes and log chunks (API polls the events table; see decision 2)
- `GET /v1/runs` — filters and cursor pagination
- `DELETE /v1/runs/{id}` — cancel
- `GET /v1/cluster` — real node/slot/queue snapshot for the UI
- `GET /healthz`, `/readyz`, `/metrics`

State machine, enforced in one module:
`queued → dispatching → running → succeeded | failed | timed_out | oom_killed | cancelled | infra_error`

`failed` is the user's code exiting non-zero. `infra_error` is the platform's fault and is what the SLO counts. stdout and stderr are each capped at 1 MiB.

### Dispatcher

- **Claim loop:** claim up to (cap − active) queued runs with `SKIP LOCKED`, create ConfigMap + Job, mark `dispatching`. Safe with multiple replicas.
- **Watch loop:** pod Running → `running`; on termination collect exit code, reason, logs, duration → terminal state → delete the Job with background propagation.
- **Reconcile** on startup and every 30 s: non-terminal run with no Job → re-queue if it never started, else `infra_error`; managed Job with no live run → delete.
- **Log streaming:** follow pod logs while running, persist chunks to an events table.
- **Sweeper** (one replica at a time via transaction-level advisory lock): delete any managed Job/Pod/ConfigMap older than its timeout + 5 minutes, and anything older than 10 minutes. Count every deletion in a metric; non-zero means an earlier layer failed.
- **KEDA** scales dispatcher replicas on queued-run count.

### MCP server

Tools: `run_code` (submit and wait; returns stdout, stderr, exit code, status), `submit_batch`, `get_run`, `cancel_run`. Ship a config snippet for Claude Code and a transcript in the README captured from a real agent session against the local cluster.

### Web UI

Dense and operational, not marketing-styled. Editor, language, timeout, Run, live log stream, status/exit code/duration/node. Cluster panel from `/v1/cluster`. Recent runs table. "Try an attack" buttons that load the attack-suite snippets.

**Replay mode (public site):** the same screens driven by `snapshot.json`. A visitor picks a recorded run, including each attack, and watches its real event stream play back with original timings. A fixed banner states: recorded on `<date>` on `<environment>`; the cluster is offline by design, idle cost $0; this page cannot execute code. Also shows the measured results table and links to the CI run and results file that produced them.

`make snapshot` exports a curated set of runs (one per terminal state, every attack case, the latest benchmark summary) from the database to `web/public/snapshot.json`, with all identifiers and hostnames that are not needed stripped out.

## 8. Tests

**Unit** (`make test`, no cluster): validation, state-machine transitions, pod spec builder (assert every hardening field), claim logic under concurrency, idempotency, reconcile decisions with a faked Kubernetes client, snapshot scrubbing.

**End-to-end** (`make e2e`): Python and Node happy paths, non-zero exit, stdin, batch of 20 with the cap respected at every sample, cancel, SSE delivers logs before completion.

**Attack suite** (`make attack`): each case submits real hostile code through the public API and asserts both the run outcome and cluster state.

| Attempt | Must hold |
|---|---|
| Outbound TCP to a public IP; DNS lookup; connect to the Kubernetes API service, another pod IP, `169.254.169.254` | All fail; run completes |
| Read the service-account token path | File does not exist |
| Write outside `/tmp` | Read-only filesystem error |
| Fill `/tmp` past its limit | Contained and reported; node unaffected |
| Allocate memory past the limit | `oom_killed` |
| Fork bomb | Contained by the PID limit; concurrent runs still succeed |
| Infinite loop | `timed_out` at about the timeout, not at the backstop |
| Unbounded output | Truncated with flag set; dispatcher memory flat |
| Find another concurrent run's code or files | Not found |
| `mount`, `chroot`, raw socket, capability check | Non-root, no capabilities, denied |
| Privileged or hostPath pod applied directly to `sandboxes` with `kubectl` | Rejected at admission |

**Cleanup proof:** after each terminal state, assert within a bounded wait that the Job, Pod and ConfigMap for that run are gone. Create an orphan Job by hand and assert the sweeper removes it.

**Chaos** (`make chaos`): kill the dispatcher with 10 runs in flight → every run reaches exactly one terminal state, none runs twice, `sandboxes` ends empty. Interrupt the database → `/readyz` returns 503 and the system recovers.

**CI (`ci.yml`)** on every push: lint, types, unit, then kind + e2e + attack + cleanup + chaos; plus `trivy`, `kubeconform`, `tflint`, Checkov, `gitleaks`, and the cost-trap checks from section 3. Add the README badge only after a real passing run.

## 9. AWS: bootstrap, session, reaper

You write and validate all of this. The owner runs it.

**`infra/bootstrap/`** (owner applies once from their Mac after reviewing the plan): versioned, encrypted S3 state bucket with native lockfile locking; GitHub OIDC provider; a CI role restricted to this repo's `main` branch and to the session workflow's environment, with a permissions policy scoped to what the EKS stack needs; AWS Budgets alerts at $5, $15 and $25 to the owner's email.

**`infra/eks/`:** VPC with public subnets only in two AZs, no NAT; EKS on a pinned standard-support version, Auto Mode off, API endpoint restricted to the runner's IP for the session; managed node groups — `system` (1 × `t3.medium`) and `sandbox` (2 × `t3.medium`, tainted, labelled); VPC CNI network policy enabled; IMDSv2 hop limit 1; control-plane logging off; everything tagged `project=ksc` and `expires-at=<timestamp>`.

**`eks-session.yml`** — `workflow_dispatch` only, protected by a GitHub Environment that requires the owner's approval, with inputs `ttl_hours` (default 2, max 4) and a typed confirmation string:

1. Assume the role via OIDC. Print the cost estimate for the requested TTL.
2. `terraform apply`. Install KEDA and the chart with `values-eks.yaml` pointing at Neon. (NetworkPolicy enforcement on EKS comes from the VPC CNI, not Calico.)
3. First test: prove NetworkPolicy is enforced on this cluster. If not, stop — nothing else is meaningful.
4. Run e2e, attack, cleanup, chaos. Run the benchmark. Upload `bench/results/<date>-eks.json` and test reports as artifacts and commit the results file.
5. Optional, time-boxed: gVisor `RuntimeClass` on the sandbox node group; rerun attack + benchmark under it and record overhead. If it does not work, record exactly why.
6. `terraform destroy` in an `if: always()` step.
7. Verify zero resources tagged `project=ksc` remain; if any do, fail the run and open a GitHub issue listing them.

**`aws-reaper.yml`** — scheduled a few times a day and runnable by hand: if any tagged resource is past `expires-at`, run destroy and open an issue. Note in the README that GitHub pauses scheduled workflows in inactive repos, so the budget alerts are the final backstop.

**`docs/AWS.md`:** what the session proves, what a real deployment would change (private nodes + NAT or endpoints, Karpenter, multi-AZ database, per-tenant isolation, gVisor or Firecracker by default), and the measured session cost taken from Cost Explorer after the first run.

## 10. Observability

Metrics: `runs_submitted_total{language}`, `runs_finished_total{language,status}`, `runs_active`, `queue_depth`, `concurrency_cap`, histograms `run_queue_wait_seconds`, `run_schedule_seconds`, `run_exec_seconds`, `run_e2e_seconds`, `sandbox_lifetime_seconds`, plus `sweeper_deletions_total{kind}`, `reconcile_corrections_total{reason}`, `dispatcher_k8s_api_errors_total`.

Deliver a provisioned Grafana dashboard, three alerts (queue wait p95 high; any sandbox older than timeout + 5 min; `infra_error` ratio high), and `docs/RUNBOOK.md` with a section per alert. One SLO in the README — 99% of runs reach a terminal state without `infra_error` — with the measured value and sample size.

**Benchmark (`make bench`):** 200 runs at concurrency 5 and 200 at concurrency 20 of a short CPU-bound script. Record p50/p95/p99 for queue wait, schedule time and end-to-end, and cold start with the image pre-pulled versus not. Results go to `bench/results/`, and the README table is generated from those files, one column group per environment.

## 11. Phases

| # | Phase | Done when |
|---|---|---|
| 0 | Preflight: tool versions, Docker running, repo scaffold, `CLAUDE.md`, `PROGRESS.md`, ADR stubs | Owner checkpoint A answered |
| 1 | kind cluster, CNI, chart, Postgres, migrations, API health, namespaces, RBAC, PSA, NetworkPolicies, quota | `make up` works from clean; `make down` leaves nothing |
| 2 | One Python run end to end | e2e gets `hello` back; cleanup proof passes for `succeeded` |
| 3 | Timeout, OOM, exit codes, cancel, infra errors, sweeper, reconcile, idempotency | Cleanup proof passes for every terminal state; orphan test passes |
| 4 | Admission policy, attack suite, chaos tests, `ci.yml` | `make attack` and `make chaos` green locally and in a real CI run |
| 5 | Node runtime, batch, SSE, UI live mode, MCP server | UI streams a run; attack buttons work; agent transcript captured |
| 6 | KEDA, metrics, dashboard, alerts, runbook, kind benchmark | Results file committed; README table generated from it |
| 7 | Snapshot export, replay mode, Cloudflare deploy workflow | Public site live after owner checkpoint C; it shows recorded runs and cannot execute |
| 8 | Bootstrap + EKS Terraform, session and reaper workflows, cost-trap checks, `docs/COST.md` | Everything validates and plans cleanly; then stop at owner checkpoint D |
| 9 | After the owner's session: ingest EKS results, final README, threat model, demo script | Final report delivered |

## 12. Owner checkpoints (the only times you stop and ask)

- **A — Start.** Confirm repo name and that it is public; confirm tools are installed.
- **B — Neon.** Ask for a Neon project connection string (pooled and direct) to put in `.env` and GitHub secrets. Needed by phase 7 for snapshots from a persistent history; earlier phases use the local container.
- **C — Cloudflare.** Ask for an API token scoped to deploying this one site, and the account ID.
- **D — AWS go/no-go.** Present: the Terraform plan summary, the per-session cost estimate, the budget alerts, the reaper, and a checklist for the owner (confirm credits cover EKS/EC2; apply `infra/bootstrap`; add the role ARN to GitHub; trigger `eks-session`). Then wait.

## 13. Documentation deliverables

- **`README.md`** in this order: what and why in one paragraph that includes the positioning line from section 1a; link to the live replay site; 60-second local quickstart; architecture diagram (Mermaid) with the trust boundary; how isolation works; the attack table with linked test names; measured results with provenance; SLO; cost model (idle $0, per-session figure from Cost Explorer); design decisions linking to ADRs, including a short "Why not agent-sandbox?" answer (workload mismatch: it manages long-lived stateful sandboxes, this runs one-shot jobs; this project exists to build and test the isolation and cleanup layers directly; for a long-lived agent workspace, use agent-sandbox); **Known limitations** (shared kernel under runc, single-tenant, kind is not production, public-subnet nodes are a cost trade-off, etc.); status line — independent portfolio project, fictional demo code, not production-ready, not enterprise-tested, do not expose the API to the internet.
- **`docs/THREAT_MODEL.md`:** assets, attacker (arbitrary code inside a sandbox), threat → control → proving test → residual risk. State what is not defended: kernel exploits without gVisor, side channels, a compromised dispatcher.
- **`docs/adr/`**, **`RUNBOOK.md`**, **`AWS.md`**, **`COST.md`**, **`demo.md`** (a 90-second recording script: run code, fire the attack buttons, show Grafana, kill the dispatcher mid-run, show `sandboxes` empty).

## 14. Agent tooling

Build this with plain Claude Code and this file. No workflow packs (gstack, ECC or similar) are required; the phases, gates and tests here already do that job, and extra always-loaded skills dilute the instructions. One optional exception at the end of phase 9: an independent review pass by a fresh session that has not seen the build — a code review plus a security audit of the chart, dispatcher and Terraform — with findings fixed or listed under Known limitations.

## 15. Final report

When finished, give the owner: what was built; exact commands to reproduce the demo; test counts and results from the last real run; benchmark numbers with provenance for each environment; the actual AWS spend; everything deferred or not working; and the three weakest parts of the project in your honest opinion.

---

## Addendum A — Local machine constraints (these override anything above that conflicts)

**Dev machine:** the owner's Mac mini. Its internal disk is nearly full. Everything heavy lives on an external SSD. The owner launches you from a shell that has sourced `$CX/env.sh`, which redirects tool caches and the container VM to the drive. `$CX` is the `cx` folder on the drive.

1. **Never write anything large to the internal disk.** That means VM disks, container images, package caches, Terraform providers, Python builds, `node_modules` outside the repo, and downloaded archives. The repo itself is on the drive.
2. **Container runtime is Colima** (a Linux VM whose disk lives under `$COLIMA_HOME` on the drive) with the Docker CLI and kind. Not Docker Desktop. The VM is cgroup v2, which current kind and Kubernetes need.
3. **No host bind mounts.** Paths under `/Volumes` are not mounted into the VM. Do not use `extraMounts` or `docker run -v` from the repo. Get files into clusters with ConfigMaps, `docker cp`, `kubectl cp`, or the API.
4. **Phase 0 preflight must verify, and fail loudly if any check fails:**
   - the drive is mounted and `$CX` exists
   - `COLIMA_HOME`, `UV_CACHE_DIR`, `npm_config_cache`, `HOMEBREW_CACHE`, `TF_PLUGIN_CACHE_DIR`, `HELM_CACHE_HOME`, `KUBECONFIG` and `KUBECACHEDIR` are set and all point under `$CX`
   - `docker info` reports cgroup version 2 and Colima is the active context
   - `~/.colima` does not exist
   - free space: drive above 15 GB, internal disk above 3 GB (warn, not fail, for the internal disk)
5. **Add `make doctor`** (runs the checks above plus prints sizes of the internal-disk locations that tools like to grow: `~/Library/Caches`, `~/.docker`, `~/.cache`, `~/.npm`) and run it at the start and end of every phase. If internal-disk usage grew more than 500 MB during a phase, record the cause in `PROGRESS.md` and fix it.
6. **Add `make disk`** (docker system df, node images, cache sizes), **`make clean`** (prune build cache, dangling images, old bench logs) and **`make nuke`** (delete the kind cluster and prune everything, with a typed confirmation). Never prune or delete the Colima VM without confirmation.
7. **Parameterise cluster size.** `make up WORKERS=3` is the default; the kind config is generated from a template. The spread test and the benchmark must read the worker count and assert against it, not a hard-coded 3. Record the Colima CPU/memory settings and worker count next to every benchmark result. The owner may run `WORKERS=1` or `2` on a small Mac.
8. **Keep images small.** Pin by digest, prefer `-slim`, load sandbox images with `kind load docker-image`, keep any buildx cache on the drive. No large base images.
9. **Never commit large files.** Add a pre-commit or CI check that rejects files over 5 MB. Results files and reports stay small.
10. **Safe shutdown.** `make down`, then `colima stop`, before the drive is unplugged. If the drive disappears mid-run, stop work and tell the owner. Do not try to repair the VM disk.
11. **Checkpoint A additions:** also confirm `gh auth status` works (you will need to create the GitHub repo and push so CI can run), and ask which worker count to default to given the Mac's RAM.

---

## Kickoff prompt (paste into Claude Code, launched from the repo folder after `source $CX/env.sh`)

> Read `docs/HANDOFF.md` in full, including Addendum A. It is the source of truth for this project. Start at phase 0: run the preflight including the storage checks in Addendum A, scaffold the repo, create `CLAUDE.md`, `PROGRESS.md` and `make doctor`, then ask me owner checkpoint A. After that, work through Tier 1 (phases 0 to 4, see section 1a) in order, committing at the end of each, stopping only at owner checkpoint A or if you are blocked. When Tier 1 is green in a real CI run, stop, write the final report, and wait for me; do not start phases 5 to 9 unless I say so. Never run anything that creates AWS resources. Never write large files to the internal disk. At the start of every later session, read `PROGRESS.md` first and run `make doctor`.
