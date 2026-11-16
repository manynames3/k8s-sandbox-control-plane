import json
import time

import pytest

from tests.e2e.conftest import assert_cleaned_up

pytestmark = pytest.mark.e2e


def test_sse_delivers_logs_before_completion(client):
    code = ("import sys, time\nprint('first', flush=True)\ntime.sleep(8)\n"
            "print('second', flush=True)\nprint('oops', file=sys.stderr, flush=True)")
    rid = client.post("/v1/runs", json={"language": "python3.12", "code": code}).json()["id"]
    seen, ended = [], False  # seen: (arrival time, event kind, data)
    with client.stream("GET", f"/v1/runs/{rid}/events", timeout=90) as r:
        assert r.status_code == 200
        kind = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: ") and kind:
                seen.append((time.monotonic(), kind, json.loads(line[6:])))
            if kind == "end":
                ended = True
                break
    kinds = [k for _, k, _ in seen]
    out = "".join(d["text"] for _, k, d in seen if k == "stdout")
    err = "".join(d["text"] for _, k, d in seen if k == "stderr")
    assert out == "first\nsecond\n" and err == "oops\n", seen
    states = [d["status"] for _, k, d in seen if k == "state"]
    assert states == ["queued", "dispatching", "running", "succeeded"], states
    first_log = next(t for t, k, d in seen if k == "stdout" and "first" in d["text"])
    final = next(t for t, k, d in seen if k == "state" and d["status"] == "succeeded")
    assert final - first_log > 4, "'first' must arrive well before the run ends (8 s sleep)"
    assert ended and kinds[-1] == "state"  # the stream closed itself after the terminal state
    assert_cleaned_up(rid)
