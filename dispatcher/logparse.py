"""Parse the runner's JSON-lines framing back into stdout / stderr."""
import base64
import json
from collections.abc import Iterator
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


def complete_lines(raw: str) -> list[str]:
    """Fully written lines. The final split element is either empty (raw ends with a newline) or a
    partial line cut off by a byte-limited read, so it is always dropped."""
    return raw.split("\n")[:-1]


def iter_chunks(lines: list[str], start: int = 0) -> Iterator[tuple[int, str, str]]:
    """Yield (line_number, 'stdout'|'stderr', text) for output frames from line `start` on."""
    for n in range(start, len(lines)):
        try:
            obj = json.loads(lines[n])
        except json.JSONDecodeError:
            continue
        if obj.get("s") in ("out", "err"):
            text = base64.b64decode(obj.get("b", "")).decode("utf-8", errors="replace")
            yield n, "stdout" if obj["s"] == "out" else "stderr", text
