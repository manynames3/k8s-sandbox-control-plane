"""Snapshot scrubbing: the public site must not carry identifiers, hostnames, keys or real dates."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import snapshot_lib as lib

RID = "11111111-2222-3333-4444-555555555555"
RUN = {
    "id": RID, "language": "python3.12", "timeout_seconds": 30, "status": "succeeded", "exit_code": 0,
    "termination_reason": None, "truncated": False, "node": "ksc-worker2",
    "stdout": f"job run-{RID}-abcde on ksc-worker2 key ksc_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n",
    "stderr": "", "created_at": "2026-11-16T08:00:00+00:00", "started_at": "2026-11-16T08:00:01+00:00",
    "finished_at": "2026-11-16T08:00:03.5+00:00",
}
EVENTS = [
    {"event": "state", "data": {"status": "queued", "ts": "2026-11-16T08:00:00+00:00"}},
    {"event": "state", "data": {"status": "running", "ts": "2026-11-16T08:00:01+00:00"}},
    {"event": "stdout", "data": {"text": f"node ksc-worker2 {RID}\n", "ts": "2026-11-16T08:00:02+00:00"}},
    {"event": "end", "data": {}},
]


def test_text_scrubbing_removes_ids_hosts_pods_and_keys():
    nodes = lib.NodeAliases()
    out = lib.scrub_text(RUN["stdout"] + f"also {RID} and ksc-control-plane password=hunter2hunter2", nodes)
    assert RID not in out and "ksc-worker2" not in out and "ksc_AAAA" not in out
    assert "hunter2" not in out and "ksc-control-plane" not in out
    assert "<api-key>" in out and "<pod>" in out and "worker-1" in out


def test_node_aliases_are_stable_and_ordered():
    nodes = lib.NodeAliases()
    assert [nodes("b"), nodes("a"), nodes("b"), nodes(None)] == ["worker-1", "worker-2", "worker-1", None]


def test_run_is_scrubbed_with_relative_times_and_stable_short_id():
    nodes = lib.NodeAliases()
    run = lib.scrub_run(RUN, EVENTS, "Hello", "state", None, nodes)
    assert run["id"] == lib.short_id(RID) and len(run["id"]) == 8 and RID not in json.dumps(run)
    assert [e["t"] for e in run["events"]] == [0.0, 1.0, 2.0]  # no absolute timestamps, no 'end' event
    assert run["node"] == "worker-1" and run["duration_seconds"] == 2.5
    assert "2026-11-16" not in json.dumps(run)


def test_build_requires_a_clean_snapshot():
    nodes = lib.NodeAliases()
    run = lib.scrub_run(RUN, EVENTS, "Hello", "state", None, nodes)
    snap = lib.build_snapshot([run], None, {"date": "2026-11-16", "environment": "kind-on-mac"}, {})
    lib.assert_clean(snap, forbidden=["aiden"])
    leaky = {**snap, "oops": "ksc-worker2"}
    with pytest.raises(ValueError, match="identifying"):
        lib.assert_clean(leaky, forbidden=[])
    with pytest.raises(ValueError, match="identifying"):
        lib.assert_clean({**snap, "owner": "aiden"}, forbidden=["aiden"])


def test_benchmark_summary_keeps_only_public_fields():
    res = {"date": "d", "environment": "e", "git_sha": "x", "levels": [], "host": {"arch": "arm64"},
           "vm": {"cpus": 4, "memory_bytes": 1, "disk_bytes": 9}, "timestamp": "t", "monitoring_stack_running": True}
    out = lib.summarize_benchmark(res, "bench/results/f.json")
    assert "host" not in out and "timestamp" not in out and out["vm"] == {"cpus": 4, "memory_bytes": 1}
    assert out["results_file"] == "bench/results/f.json"


def test_large_output_is_capped_and_says_so():
    nodes = lib.NodeAliases()
    big = "x" * 100_000
    run = lib.scrub_run({**RUN, "stdout": big, "truncated": True}, EVENTS[:2] + [
        {"event": "stdout", "data": {"text": big, "ts": "2026-11-16T08:00:02+00:00"}},
        {"event": "stdout", "data": {"text": big, "ts": "2026-11-16T08:00:03+00:00"}}], "Spam", "attack", "output", nodes)
    assert len(run["stdout"]) < lib.SHOWN_BYTES + 200 and "more bytes not shown" in run["stdout"]
    streamed = "".join(e["text"] for e in run["events"] if e["kind"] == "stdout")
    assert len(streamed.encode()) == lib.SHOWN_BYTES  # later chunks dropped once the cap is reached
