"""Parse the runner's JSON-lines framing back into stdout / stderr."""
import base64
import json
from dataclasses import dataclass


@dataclass
class ParsedLogs:
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    timeout: bool = False


def parse(raw: str, max_bytes: int = 1024 * 1024) -> ParsedLogs:
    out, err = bytearray(), bytearray()
    res = ParsedLogs()
    for line in raw.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # a partial last line when the log read was byte-limited
        kind = obj.get("s")
        if kind in ("out", "err"):
            buf = out if kind == "out" else err
            chunk = base64.b64decode(obj.get("b", ""))
            room = max_bytes - len(buf)
            if room > 0:
                buf += chunk[:room]
            if len(chunk) > room:
                res.truncated = True
        elif kind == "meta":
            res.truncated = res.truncated or bool(obj.get("truncated"))
            res.timeout = res.timeout or bool(obj.get("timeout"))
    res.stdout = out.decode("utf-8", errors="replace")
    res.stderr = err.decode("utf-8", errors="replace")
    return res
