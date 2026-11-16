#!/usr/bin/env python3
"""Renders the README results table from bench/results/*.json (one column group per environment).
Every number in the table comes from those files; nothing is typed by hand.

  python3 bench/render_table.py            # print markdown
  python3 bench/render_table.py --readme   # replace the block between the BENCH markers in README.md
"""
import glob
import json
import sys
from pathlib import Path

START, END = "<!-- BENCH:START -->", "<!-- BENCH:END -->"
ROOT = Path(__file__).resolve().parents[1]


def latest_per_environment() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(glob.glob(str(ROOT / "bench/results/*.json"))):
        data = json.loads(Path(path).read_text())
        data["_file"] = Path(path).relative_to(ROOT).as_posix()
        out[data["environment"]] = data  # sorted by name = by date; the latest wins
    return out


def fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def tri(lv: dict, key: str) -> str:
    s = lv[key]
    return f"{fmt(s['p50'])} / {fmt(s['p95'])} / {fmt(s['p99'])}"


def render() -> str:
    envs = latest_per_environment()
    if not envs:
        return "_No benchmark results yet (`make bench`)._"
    lines = []
    for env, d in envs.items():
        vm = d.get("vm") or {}
        mem = f"{vm['memory_bytes'] / 2**30:.0f} GiB" if vm.get("memory_bytes") else "n/a"
        lines += [
            f"**{env}**, measured {d['date']} at `{d['git_sha']}`{' (uncommitted changes)' if d.get('git_dirty') else ''}. "
            f"Kubernetes {d['kubernetes_version']}, {d['container_runtime']}, {d['worker_nodes']} workers, "
            f"concurrency cap {d['concurrency_cap']}, VM {vm.get('cpus', 'n/a')} CPU / {mem}. "
            f"Workload: {d['workload']}. Source: [`{d['_file']}`]({d['_file']}).",
            "",
            "| client concurrency | runs | succeeded | queue wait p50 / p95 / p99 (s) | schedule p50 / p95 / p99 (s) | end-to-end p50 / p95 / p99 (s) | throughput (runs/s) |",
            "|---|---|---|---|---|---|---|",
        ]
        for lv in d["levels"]:
            lines.append(f"| {lv['concurrency']} | {lv['sample_size']} | {lv['statuses'].get('succeeded', 0)} | "
                         f"{tri(lv, 'queue_wait_seconds')} | {tri(lv, 'schedule_seconds')} | {tri(lv, 'e2e_seconds')} | "
                         f"{lv['throughput_runs_per_second']} |")
        cold = d.get("cold_start", {})
        if not cold.get("measured"):
            lines += ["", f"Cold start (image pulled versus pre-pulled): not measured here. {cold.get('reason', '')}"]
        total = sum(lv["sample_size"] for lv in d["levels"])
        infra = sum(lv["statuses"].get("infra_error", 0) for lv in d["levels"])
        lines += ["", f"SLO sample from this benchmark: {total - infra} of {total} runs finished without `infra_error` "
                      f"({100 * (total - infra) / total:.2f}%).", ""]
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    table = render()
    if "--readme" in sys.argv:
        readme = ROOT / "README.md"
        text = readme.read_text()
        a, b = text.index(START) + len(START), text.index(END)
        readme.write_text(text[:a] + "\n" + table + text[b:])
        print("README.md updated")
    else:
        print(table)
