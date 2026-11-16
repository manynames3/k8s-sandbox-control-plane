import json
import logging
import os
import signal
import time

from api.db import get_engine
from dispatcher import core, podspec
from dispatcher.kube import RealKube


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": self.formatTime(record), "level": record.levelname, "msg": record.getMessage()}
        for k in ("run_id", "status", "error"):
            if hasattr(record, k):
                d[k] = getattr(record, k)
        return json.dumps(d)


def main() -> None:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[h])
    ns = os.environ.get("SANDBOX_NAMESPACE", "sandboxes")
    cap = int(os.environ.get("CONCURRENCY_CAP", "4"))
    sandbox = podspec.SandboxSettings(
        namespace=ns,
        images={"python3.12": os.environ.get("RUNNER_IMAGE_PYTHON", "ksc/runner-python:dev")},
    )
    engine, kube = get_engine(), RealKube(ns)
    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logging.getLogger("ksc.dispatcher").info("dispatcher started")
    while not stop:
        try:
            core.tick(engine, kube, cap, sandbox)
        except Exception:
            logging.getLogger("ksc.dispatcher").exception("tick failed")
        time.sleep(1)


if __name__ == "__main__":
    main()
