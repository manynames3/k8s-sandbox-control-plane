# 90-second demo script

A recording script for the local cluster. Everything shown is real; nothing is mocked. Prepare once: `make monitoring`
(Grafana), `make snapshot` is not needed.

**Setup (not recorded):**
```bash
source $CX/env.sh            # local drive/Colima setup
make monitoring              # cluster + KEDA + Prometheus/Grafana
KEY=$(kubectl -n ksc-system exec deploy/ksc-api -- python -m api.keys create demo)
kubectl -n ksc-system port-forward svc/ksc-api 8080:8080 &
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80 &
(cd web && npm run build && npm run preview &)            # UI on http://127.0.0.1:4173 (paste $KEY)
watch -n1 kubectl -n sandboxes get pods                   # keep visible in a terminal pane
```

| Time | Do | Say / show |
|---|---|---|
| 0:00 | Open the UI, paste the key, press **Run** on the hello snippet | "Code goes in, runs in its own short-lived pod, output streams back live." Point at the cluster panel: one slot used, the node it landed on. |
| 0:15 | Click **Reach the network**, Run | "Same API, hostile code. Every outbound attempt is closed. The loopback control is open, so the probe works; the policy is what blocks it." |
| 0:30 | Click **Eat all memory**, then **Fork bomb**, Run each | "Memory limit kills it: `oom_killed`. The fork bomb is contained by the PID limit and ends at its timeout; neighbouring runs keep working." |
| 0:45 | Click **Write outside /tmp**, then show the recent-runs table | "Read-only filesystem, errno 30. Every run ends in exactly one terminal state." |
| 0:55 | Open Grafana (dashboard "ksc: sandbox control plane") | Queue depth, slots, latency percentiles, the SLO tile, and the sweeper tile at 0 ("non-zero would mean an earlier cleanup layer failed"). |
| 1:10 | Submit a batch of ~12 slow runs (`curl .../v1/runs:batch`), then `kubectl -n ksc-system delete pod -l app=ksc-dispatcher --force --grace-period=0` | "Kill the dispatcher mid-flight." |
| 1:20 | Show `kubectl -n sandboxes get pods` and the runs finishing | "It comes back, every run reaches one terminal state, none runs twice." |
| 1:28 | `kubectl -n sandboxes get jobs,pods,configmaps` | "And the sandbox namespace is empty." |

Notes for the take: the network attack takes about 30 s (DNS timeouts), so show its output streaming and cut. Do not show the API
key. Keep the cluster panel visible when the batch runs so the slot bar and the second dispatcher replica (KEDA) show.
