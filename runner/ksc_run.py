"""Sandbox entrypoint wrapper.

Runs the user program with a hard wall-clock timeout (layer 1 of 3) and frames its output as
JSON lines on our own stdout so the dispatcher can separate stdout from stderr using only the
Kubernetes log API:

    {"s": "out"|"err", "b": "<base64 chunk>"}     user output
    {"s": "meta", "truncated": true, "timeout": true}   wrapper events

Exit codes: the child's exit code, or 124 on timeout (distinct from an OOM kill, which the
container status reports as OOMKilled / 137). Each stream is capped at MAX_STREAM_BYTES; excess
output is drained and discarded so the child never blocks on a full pipe.
"""
import base64
import json
import os
import signal
import subprocess
import sys
import threading
import time

MAX_STREAM_BYTES = 1024 * 1024
CHUNK = 4096
TIMEOUT_EXIT = 124
CODE_DIR = os.environ.get("KSC_CODE_DIR", "/code")
_lock = threading.Lock()


def emit(obj: dict) -> None:
    line = json.dumps(obj, separators=(",", ":")) + "\n"
    with _lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def pump(stream, tag: str, state: dict) -> None:
    sent = 0
    while True:
        data = stream.read1(CHUNK)  # read() would block until CHUNK bytes or EOF: no live output
        if not data:
            return
        room = MAX_STREAM_BYTES - sent
        if room > 0:
            part = data[:room]
            emit({"s": tag, "b": base64.b64encode(part).decode()})
            sent += len(part)
        if len(data) > room and not state.get("truncated"):
            state["truncated"] = True
            emit({"s": "meta", "truncated": True})


def main() -> int:
    timeout = float(os.environ.get("KSC_TIMEOUT_SECONDS", "30"))
    cmd = json.loads(os.environ["KSC_COMMAND"])  # e.g. ["python", "/code/main.py"]
    stdin_path = os.path.join(CODE_DIR, "stdin")
    stdin_file = open(stdin_path, "rb") if os.path.exists(stdin_path) else None  # noqa: SIM115
    try:
        proc = subprocess.Popen(
            cmd, stdin=stdin_file or subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True, cwd="/tmp",
        )
    finally:
        if stdin_file:
            stdin_file.close()  # the child holds its own descriptor
    state: dict = {}
    threads = [
        threading.Thread(target=pump, args=(proc.stdout, "out", state), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, "err", state), daemon=True),
    ]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    timed_out = False
    while proc.poll() is None:
        if time.monotonic() >= deadline:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            break
        time.sleep(0.05)
    proc.wait()
    # Kill any stragglers holding the pipes open, then let the pumps finish.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for t in threads:
        t.join(timeout=2)
    if timed_out:
        emit({"s": "meta", "timeout": True})
        return TIMEOUT_EXIT
    return proc.returncode if proc.returncode >= 0 else 128 + (-proc.returncode)


if __name__ == "__main__":
    sys.exit(main())
