"""Pure functions that turn recorded runs into the public snapshot, with identifiers scrubbed.

The public replay site ships only what this produces. Anything not needed to show a run is removed:
run and job ids, node and pod names, API keys, absolute timestamps."""
import hashlib
import json
import re
from datetime import datetime
from typing import Any

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
KEY_RE = re.compile(r"ksc_[A-Za-z0-9_-]{20,}")
HOST_RE = re.compile(r"\bksc-(?:control-plane|worker\d*)\b")
POD_RE = re.compile(r"\brun-[0-9a-f-]{36}(?:-[a-z0-9]{5})?\b")
SECRET_RE = re.compile(r"(?i)\b(password|passwd|secret|token)\s*[=:]\s*\S{8,}")


class NodeAliases:
    """Maps real node names to worker-1, worker-2, ... in order of first appearance."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def __call__(self, name: str | None) -> str | None:
        if not name:
            return None
        return self._map.setdefault(name, f"worker-{len(self._map) + 1}")


def short_id(run_id: str, salt: str = "ksc-showcase") -> str:
    return hashlib.sha256((salt + run_id).encode()).hexdigest()[:8]


def scrub_text(text: str, nodes: NodeAliases) -> str:
    text = KEY_RE.sub("<api-key>", text)
    text = POD_RE.sub("<pod>", text)
    text = UUID_RE.sub("<id>", text)
    text = HOST_RE.sub(lambda m: nodes(m.group(0)) or "<node>", text)
    return SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)


SHOWN_BYTES = 8 * 1024  # per stream: enough to read, small enough for a static site


def cap_text(text: str, limit: int = SHOWN_BYTES) -> str:
    raw = text.encode()
    if len(raw) <= limit:
        return text
    return raw[:limit].decode(errors="ignore") + f"\n... [{len(raw) - limit:,} more bytes not shown in this recording]\n"


def _ts(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def scrub_run(run: dict[str, Any], events: list[dict[str, Any]], label: str, kind: str,
              attack_id: str | None, nodes: NodeAliases) -> dict[str, Any]:
    """`run` is the API's run JSON; `events` the SSE events as {event, data}. Times become offsets
    in seconds from the first event, so the replay keeps the original pacing without absolute dates."""
    first = min((_ts(e["data"]["ts"]) for e in events if "ts" in e["data"]), default=0.0)
    out_events: list[dict[str, Any]] = []
    shown = {"stdout": 0, "stderr": 0}
    for e in events:
        d = e["data"]
        if e["event"] not in ("state", "stdout", "stderr") or "ts" not in d:
            continue
        item: dict[str, Any] = {"t": round(_ts(d["ts"]) - first, 3), "kind": e["event"]}
        if e["event"] == "state":
            item["status"] = d["status"]
        else:
            text = scrub_text(d["text"], nodes)
            room = SHOWN_BYTES - shown[e["event"]]
            if room <= 0:
                continue  # the run produced more than the recording keeps
            item["text"] = text.encode()[:room].decode(errors="ignore")
            shown[e["event"]] += len(item["text"].encode())
        out_events.append(item)
    start = run.get("started_at") or run["created_at"]
    duration = (_ts(run["finished_at"]) - _ts(start)) if run.get("finished_at") else None
    return {
        "id": short_id(run["id"]), "label": label, "kind": kind, "attack_id": attack_id,
        "language": run["language"], "timeout_seconds": run["timeout_seconds"], "status": run["status"],
        "exit_code": run["exit_code"], "termination_reason": run["termination_reason"],
        "truncated": run["truncated"], "node": nodes(run.get("node")),
        "stdout": cap_text(scrub_text(run.get("stdout") or "", nodes)),
        "stderr": cap_text(scrub_text(run.get("stderr") or "", nodes)),
        "duration_seconds": round(duration, 3) if duration is not None else None,
        "created_at_offset": 0.0, "events": out_events,
    }


def summarize_benchmark(results: dict[str, Any], results_file: str) -> dict[str, Any]:
    keep = ("date", "environment", "git_sha", "kubernetes_version", "container_runtime", "worker_nodes",
            "concurrency_cap", "workload", "levels", "cold_start")
    out = {k: results[k] for k in keep if k in results}
    out["vm"] = {k: v for k, v in (results.get("vm") or {}).items() if k in ("cpus", "memory_bytes")}
    out["results_file"] = results_file
    return out


def build_snapshot(runs: list[dict[str, Any]], benchmark: dict[str, Any] | None, meta: dict[str, Any],
                   links: dict[str, str]) -> dict[str, Any]:
    return {"version": 1, "recorded_on": meta["date"], "environment": meta["environment"],
            "meta": {k: meta[k] for k in ("kubernetes_version", "container_runtime", "worker_nodes",
                                          "concurrency_cap", "git_sha") if k in meta},
            "links": links, "runs": runs, "benchmark": benchmark}


def assert_clean(snapshot: dict[str, Any], forbidden: list[str]) -> None:
    """Final gate before writing: nothing identifying may remain anywhere in the snapshot."""
    blob = json.dumps(snapshot)
    problems = [pat.pattern for pat in (UUID_RE, KEY_RE, HOST_RE, POD_RE) if pat.search(blob)]
    problems += [f for f in forbidden if f and f in blob]
    if problems:
        raise ValueError(f"snapshot contains identifying data: {problems}")
