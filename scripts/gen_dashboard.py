"""Generates charts/ksc/dashboards/ksc.json (the provisioned Grafana dashboard).
Run: python3 scripts/gen_dashboard.py"""
import json
import pathlib

DS = {"type": "prometheus", "uid": "prometheus"}
_id = 0


def _next() -> int:
    global _id
    _id += 1
    return _id


def target(expr: str, legend: str = "", ref: str = "A") -> dict:
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref}


def stat(title, expr, x, w=6, unit="short", thresholds=None, desc=""):
    steps = thresholds or [{"color": "green", "value": None}]
    return {
        "id": _next(), "type": "stat", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": 0, "w": w, "h": 4}, "targets": [target(expr)],
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background"},
    }


def ts(title, targets, x, y, w=12, h=8, unit="short", desc=""):
    return {
        "id": _next(), "type": "timeseries", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(e, lg, chr(65 + i)) for i, (e, lg) in enumerate(targets)],
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 1, "fillOpacity": 10}}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
    }


def quantiles(metric):
    return [
        (f"histogram_quantile({q}, sum by (le) (rate({metric}_bucket[$__rate_interval])))", f"p{int(q * 100)}")
        for q in (0.5, 0.95, 0.99)
    ]


panels = [
    stat("Queue depth", "ksc_queue_depth", 0, desc="Runs waiting to be claimed"),
    stat("Active / cap", "ksc_runs_active / ksc_concurrency_cap", 6, unit="percentunit",
         thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.75}, {"color": "red", "value": 1}],
         desc="Sandbox slots in use"),
    stat("SLO: runs without infra_error (1h)",
         '1 - (sum(increase(ksc_runs_finished_total{status="infra_error"}[1h])) / sum(increase(ksc_runs_finished_total[1h])))',
         12, unit="percentunit",
         thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 0.99}],
         desc="Target: 99% of runs reach a terminal state without infra_error"),
    stat("Sweeper deletions (24h)", "sum(increase(ksc_sweeper_deletions_total[24h]))", 18,
         thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}],
         desc="Non-zero means an earlier cleanup layer failed"),
    ts("Queue, active runs and cap", [("ksc_queue_depth", "queued"), ("ksc_runs_active", "active"),
                                      ("ksc_concurrency_cap", "cap")], 0, 4),
    ts("Runs finished per minute, by status",
       [("sum by (status) (increase(ksc_runs_finished_total[1m]))", "{{status}}")], 12, 4),
    ts("Queue wait (submission to claim)", quantiles("ksc_run_queue_wait_seconds"), 0, 12, w=8, unit="s"),
    ts("Schedule time (Job created to container running)", quantiles("ksc_run_schedule_seconds"), 8, 12, w=8, unit="s"),
    ts("End to end (submission to terminal)", quantiles("ksc_run_e2e_seconds"), 16, 12, w=8, unit="s"),
    ts("Sweeper deletions by kind (earlier layers failed)",
       [("sum by (kind) (increase(ksc_sweeper_deletions_total[5m]))", "{{kind}}")], 0, 20, w=8),
    ts("Reconcile corrections by reason",
       [("sum by (reason) (increase(ksc_reconcile_corrections_total[5m]))", "{{reason}}")], 8, 20, w=8),
    ts("Dispatcher replicas (KEDA) and API errors",
       [('kube_deployment_status_replicas{deployment="ksc-dispatcher"}', "dispatcher replicas"),
        ("sum(increase(ksc_dispatcher_k8s_api_errors_total[5m]))", "k8s api errors / 5m")], 16, 20, w=8),
]

dashboard = {
    "uid": "ksc-overview", "title": "ksc: sandbox control plane", "tags": ["ksc"], "timezone": "browser",
    "schemaVersion": 39, "version": 1, "refresh": "10s", "time": {"from": "now-30m", "to": "now"},
    "panels": panels,
}
out = pathlib.Path(__file__).resolve().parents[1] / "charts/ksc/dashboards/ksc.json"
out.write_text(json.dumps(dashboard, indent=2) + "\n")
print("wrote", out, len(panels), "panels")
