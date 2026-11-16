"""MCP server (stdio) for k8s-sandbox-control-plane.

Lets an agent run untrusted code in a short-lived Kubernetes sandbox. It talks to the control-plane
REST API, which is only ever reachable on localhost (kubectl port-forward on a cluster).

Environment:
  KSC_API_URL  default http://127.0.0.1:8080
  KSC_API_KEY  required; create one with: kubectl -n ksc-system exec deploy/ksc-api -- python -m api.keys create <name>

Tools: run_code (submit and wait), submit_batch, get_run, cancel_run.
"""
import os
import time
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer

TERMINAL = {"succeeded", "failed", "timed_out", "oom_killed", "cancelled", "infra_error"}
AGENT_OUTPUT_LIMIT = 20_000  # characters of each stream returned to the agent; the full text stays in the API
Language = Literal["python3.12", "node20"]


class KscError(Exception):
    pass


class KscClient:
    def __init__(self, base_url: str, api_key: str, transport: httpx.BaseTransport | None = None) -> None:
        if not api_key:
            raise KscError("KSC_API_KEY is not set")
        self._http = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {api_key}"},
                                  timeout=15, transport=transport)

    def _call(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        try:
            r = self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise KscError(f"cannot reach the control plane at {self._http.base_url}: {e}") from e
        if r.status_code >= 400:
            raise KscError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()  # type: ignore[no-any-return]

    def submit(self, language: str, code: str, timeout_seconds: int, stdin: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"language": language, "code": code, "timeout_seconds": timeout_seconds}
        if stdin is not None:
            body["stdin"] = stdin
        return self._call("POST", "/v1/runs", json=body)

    def batch(self, runs: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call("POST", "/v1/runs:batch", json={"runs": runs})

    def get(self, run_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/runs/{run_id}")

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self._call("DELETE", f"/v1/runs/{run_id}")

    def wait(self, run_id: str, max_seconds: float, poll: float = 0.5) -> dict[str, Any]:
        deadline = time.monotonic() + max_seconds
        while True:
            run = self.get(run_id)
            if run["status"] in TERMINAL or time.monotonic() >= deadline:
                return run
            time.sleep(poll)


def present(run: dict[str, Any]) -> dict[str, Any]:
    """What the agent sees: the outcome, with output bounded so a noisy program cannot flood its context."""
    out: dict[str, Any] = {
        "run_id": run["id"], "status": run["status"], "exit_code": run["exit_code"],
        "termination_reason": run["termination_reason"], "language": run["language"],
    }
    for stream in ("stdout", "stderr"):
        text = run.get(stream) or ""
        out[stream] = text[:AGENT_OUTPUT_LIMIT]
        if len(text) > AGENT_OUTPUT_LIMIT:
            out[f"{stream}_cut_for_agent"] = True
    out["output_truncated"] = bool(run.get("truncated"))
    return out


def build_server(client: KscClient | None = None) -> MCPServer:
    server = MCPServer(
        "ksc-sandbox",
        instructions=(
            "Run untrusted code in an isolated, short-lived Kubernetes sandbox: no network, read-only "
            "filesystem except /tmp, 512 MiB memory, 0.5 CPU, killed at the timeout. Languages: "
            "python3.12 and node20. 'failed' means the code exited non-zero; 'infra_error' means the "
            "platform failed, not the code."
        ),
    )
    _client: list[KscClient] = []

    def api() -> KscClient:
        if client is not None:
            return client
        if not _client:
            _client.append(KscClient(os.environ.get("KSC_API_URL", "http://127.0.0.1:8080"),
                                     os.environ.get("KSC_API_KEY", "")))
        return _client[0]

    @server.tool()
    def run_code(language: Language, code: str, timeout_seconds: int = 30, stdin: str | None = None) -> dict[str, Any]:
        """Run code in a sandbox and wait for the result (stdout, stderr, exit code, final status)."""
        run = api().submit(language, code, timeout_seconds, stdin)
        # queue wait is not part of the run's own timeout, so allow generous extra time
        final = api().wait(run["id"], max_seconds=timeout_seconds + 120)
        res = present(final)
        if final["status"] not in TERMINAL:
            res["note"] = "still running or queued; use get_run to check again"
        return res

    @server.tool()
    def submit_batch(runs: list[dict[str, Any]]) -> dict[str, Any]:
        """Queue up to 20 runs at once without waiting. Each item: {language, code, timeout_seconds?, stdin?}.
        Returns run ids; poll them with get_run."""
        created = api().batch(runs)["runs"]
        return {"run_ids": [r["id"] for r in created], "count": len(created)}

    @server.tool()
    def get_run(run_id: str) -> dict[str, Any]:
        """Current status and (when finished) output of a run."""
        return present(api().get(run_id))

    @server.tool()
    def cancel_run(run_id: str) -> dict[str, Any]:
        """Cancel a queued or running run. Finished runs are unchanged."""
        return present(api().cancel(run_id))

    return server


if __name__ == "__main__":
    build_server().run()
