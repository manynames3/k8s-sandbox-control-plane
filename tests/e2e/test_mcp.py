"""The MCP server, spawned over stdio exactly as an agent would, against the real cluster."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from tests.e2e.conftest import assert_cleaned_up

pytestmark = pytest.mark.e2e
SERVER = str(Path(__file__).resolve().parents[2] / "mcp" / "ksc_mcp_server.py")


def payload(result):
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


async def with_server(api_url, api_key, fn):
    params = StdioServerParameters(command=sys.executable, args=[SERVER],
                                   env={"KSC_API_URL": api_url, "KSC_API_KEY": api_key})
    async with Client(params) as c:
        return await fn(c)


def test_agent_can_run_code_batch_and_cancel_over_stdio(api_url, api_key):
    async def scenario(c):
        tools = {t.name for t in (await c.list_tools()).tools}
        assert tools == {"run_code", "submit_batch", "get_run", "cancel_run"}
        hello = payload(await c.call_tool("run_code", {
            "language": "python3.12", "code": "print(sum(range(10)))", "timeout_seconds": 20}))
        failing = payload(await c.call_tool("run_code", {
            "language": "node20", "code": "console.error('nope'); process.exit(2)"}))
        batch = payload(await c.call_tool("submit_batch", {"runs": [
            {"language": "python3.12", "code": "print(1)"}, {"language": "node20", "code": "console.log(2)"}]}))
        slow = payload(await c.call_tool("submit_batch", {"runs": [
            {"language": "python3.12", "code": "import time\nwhile True: time.sleep(1)", "timeout_seconds": 60}]}))
        cancelled = payload(await c.call_tool("cancel_run", {"run_id": slow["run_ids"][0]}))
        return hello, failing, batch, cancelled, slow["run_ids"][0]

    hello, failing, batch, cancelled, slow_id = asyncio.run(with_server(api_url, api_key, scenario))
    assert (hello["status"], hello["stdout"]) == ("succeeded", "45\n")
    assert (failing["status"], failing["exit_code"], failing["stderr"]) == ("failed", 2, "nope\n")
    assert batch["count"] == 2
    assert cancelled["status"] == "cancelled"
    for rid in (hello["run_id"], failing["run_id"], slow_id, *batch["run_ids"]):
        assert_cleaned_up(rid)
