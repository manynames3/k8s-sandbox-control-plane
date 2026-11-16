# Runbook

Three alerts, each with the question it answers, how to confirm it, and what to do. Metric names are
prefixed `ksc_`. Alert definitions live in `charts/ksc/templates/monitoring.yaml`; the dashboard is
"ksc: sandbox control plane" in Grafana (`make monitoring`, then
`kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80`).

All commands assume `kubectl` points at the cluster (`source $CX/env.sh` locally).

## Service-level objective

**99% of runs reach a terminal state without `infra_error`.** `infra_error` is the platform's fault
(a lost Job, a pod deleted from outside, an image that cannot be pulled, a pod stuck Pending). A user's
code failing (`failed`, `timed_out`, `oom_killed`) does not count against it. The dashboard's SLO stat
shows `1 - infra_error / finished` over one hour.

## KscQueueWaitP95High

**Meaning:** the 95th-percentile time between submitting a run and the dispatcher claiming it has been
above the threshold (default 30 s) for 5 minutes. Runs are waiting for a free slot, or the dispatcher is
not claiming.

**Confirm**
```bash
kubectl -n ksc-system get pods -l app=ksc-dispatcher          # at least one Running?
kubectl -n ksc-system logs deploy/ksc-dispatcher --tail=50     # "tick failed" lines?
kubectl -n sandboxes get pods                                  # how many slots are busy
kubectl -n sandboxes describe resourcequota sandbox-quota      # pods used / hard
```
The dashboard's "Queue, active runs and cap" panel shows whether `active` sits at `cap` (saturation) or
`queued` grows while `active` is low (dispatcher problem).

**Causes and actions**
| Signal | Cause | Action |
|---|---|---|
| active = cap, queue growing | Legitimate saturation | Raise `concurrencyCap` (and the node capacity behind it), or accept the wait. KEDA adds dispatchers, not slots. |
| active < cap, queue growing, no dispatcher pod | Dispatcher down | `kubectl -n ksc-system rollout restart deploy/ksc-dispatcher`; the system recovers on its own (chaos test `test_killing_the_dispatcher...`). |
| dispatcher logs `tick failed` with database errors | Database unreachable | See "Database down" below. |
| active < cap, pods `Pending` | No node can fit a sandbox | `kubectl -n sandboxes describe pod <pod>`; free capacity or add nodes. A run Pending longer than 120 s becomes `infra_error` on its own. |

## KscSandboxOverdue

**Meaning:** the sweeper deleted a sandbox object that was older than its timeout + 5 minutes (or any
object older than 10 minutes). The sweeper is the last cleanup layer, so this means an earlier layer
failed: the wrapper timeout, the Job's `activeDeadlineSeconds`, or reconcile.

**Confirm**
```bash
kubectl -n ksc-system logs deploy/ksc-dispatcher | grep "sweeper deleted"   # which object, which run
```
Query `sum by (kind) (increase(ksc_sweeper_deletions_total[1h]))` and
`sum by (reason) (increase(ksc_reconcile_corrections_total[1h]))` in Grafana.

**Actions**
1. Find the run id in the log line and read its record: `GET /v1/runs/<id>` (status, `termination_reason`).
2. If reconcile corrections (`job_lost`, `requeue_no_job`) rose at the same time, look for something deleting
   Jobs from outside, or a dispatcher that was down for a long time.
3. If it was a `running` run that outlived its timeout, the wrapper did not fire: check the runner image
   version on the node (`kubectl -n sandboxes get pod ... -o yaml | grep image:`) and that the entrypoint is
   `ksc_run`.
4. Objects with no run id label were created by hand; delete the source.
The alert stays quiet for a clean 10 minutes after the last deletion.

## KscInfraErrorRatioHigh

**Meaning:** more than 1% of runs finished as `infra_error` over 15 minutes (sustained 10 minutes). The
SLO allows 1%.

**Confirm**
```bash
# which reasons?
kubectl -n ksc-system exec ksc-postgres-0 -- psql -U ksc -At -c \
  "select termination_reason, count(*) from runs where status='infra_error' and finished_at > now() - interval '30 min' group by 1"
```

| `termination_reason` | Meaning | Action |
|---|---|---|
| `job_lost` | A running run's Job disappeared | Something deleted it (a person, a sweeper, node loss). Check `kubectl -n sandboxes get events`. |
| `pod_deleted` | The pod was removed while running (eviction, node drain) | Check node health: `kubectl get nodes`, `kubectl describe node`. |
| `image_error` | Image could not be pulled for 30 s | Check the runner image tag/digest and registry reachability from the node. |
| `schedule_timeout` | Pod Pending for 120 s | Not enough capacity, a taint without a toleration, or a quota. `kubectl -n sandboxes describe pod`. |
| `evicted` | Node pressure eviction | Node is low on memory or disk; add capacity. |
| `no_container_status` | Pod failed without container state | Inspect the pod and kubelet logs. |

(A user filling `/tmp` is **not** `infra_error`: it is `failed` with `ephemeral_storage_exceeded`.)

## Database down

`/readyz` returns 503 while the database is unreachable; `/healthz` stays 200 so the API is not
restarted pointlessly. The dispatcher logs `tick failed` and retries every tick; runs that finish in the
cluster while the database is down are collected afterwards (the Job's finished pod is kept for
`ttlSecondsAfterFinished` = 120 s, so an outage longer than that can turn a finished run into
`infra_error/job_lost`). Restore the database; nothing else needs to be restarted
(chaos test `test_database_interruption_degrades_readiness_and_recovers`).

## Useful one-liners

```bash
kubectl -n ksc-system exec deploy/ksc-api -- python -m api.keys create <name>   # new API key
kubectl -n sandboxes get jobs,pods,configmaps -l app.kubernetes.io/managed-by=ksc   # what exists now
kubectl -n ksc-system get scaledobject,hpa                                      # KEDA state
```
