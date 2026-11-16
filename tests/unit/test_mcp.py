"""MCP server tools against a faked control-plane API (httpx MockTransport)."""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp"))
import ksc_mcp_server as m


def run_json(status="succeeded", **kw):
    base = {"id": "r1", "language": "python3.12", "status": status, "exit_code": 0,
            "termination_reason": None, "stdout": "hello\n", "stderr": "", "truncated": False}
    return {**base, **kw}


def make_client(handler):
    return m.KscClient("http://api.test", "k", transport=httpx.MockTransport(handler))


def call(server, tool, args):
    async def go():
        return await server.call_tool(tool, args)
    return asyncio.run(go())


def payload(result):
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


def test_run_code_submits_waits_and_returns_outcome():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, req.headers["authorization"]))
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["language"] == "python3.12" and body["timeout_seconds"] == 5
            return httpx.Response(202, json=run_json("queued"))
        polls = sum(1 for s in seen if s[0] == "GET")
        return httpx.Response(200, json=run_json("running" if polls < 2 else "succeeded"))

    res = payload(call(m.build_server(make_client(handler)), "run_code",
                       {"language": "python3.12", "code": "print('hello')", "timeout_seconds": 5}))
    assert res["status"] == "succeeded" and res["stdout"] == "hello\n" and res["exit_code"] == 0
    assert all(h == "Bearer k" for _, _, h in seen)


def test_agent_output_is_bounded_but_flags_it():
    big = "x" * (m.AGENT_OUTPUT_LIMIT + 5000)
    out = m.present(run_json(stdout=big, truncated=True))
    assert len(out["stdout"]) == m.AGENT_OUTPUT_LIMIT
    assert out["stdout_cut_for_agent"] is True and out["output_truncated"] is True


def test_failures_surface_as_status_not_exceptions():
    def handler(req):
        if req.method == "POST":
            return httpx.Response(202, json=run_json("queued"))
        return httpx.Response(200, json=run_json("timed_out", exit_code=124, termination_reason="timeout",
                                                 stdout=""))
    res = payload(call(m.build_server(make_client(handler)), "run_code",
                       {"language": "python3.12", "code": "while True: pass", "timeout_seconds": 1}))
    assert res["status"] == "timed_out" and res["termination_reason"] == "timeout"


def test_batch_get_and_cancel():
    def handler(req):
        if req.url.path == "/v1/runs:batch":
            return httpx.Response(202, json={"runs": [run_json("queued", id="a"), run_json("queued", id="b")]})
        if req.method == "DELETE":
            return httpx.Response(200, json=run_json("cancelled", id="a"))
        return httpx.Response(200, json=run_json())
    server = m.build_server(make_client(handler))
    items = [{"language": "python3.12", "code": "pass"}] * 2
    assert payload(call(server, "submit_batch", {"runs": items})) == {"run_ids": ["a", "b"], "count": 2}
    assert payload(call(server, "get_run", {"run_id": "a"}))["status"] == "succeeded"
    assert payload(call(server, "cancel_run", {"run_id": "a"}))["status"] == "cancelled"


def test_api_errors_and_missing_key_are_clear():
    with pytest.raises(m.KscError, match="KSC_API_KEY"):
        m.KscClient("http://api.test", "")
    c = make_client(lambda req: httpx.Response(401, json={"detail": "invalid api key"}))
    with pytest.raises(m.KscError, match="401"):
        c.get("x")


def test_server_exposes_exactly_the_four_tools():
    names = {t.name for t in asyncio.run(m.build_server(make_client(lambda r: httpx.Response(200))).list_tools())}
    assert names == {"run_code", "submit_batch", "get_run", "cancel_run"}
