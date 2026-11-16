"""Run state machine. The only place transitions are defined."""
QUEUED, DISPATCHING, RUNNING = "queued", "dispatching", "running"
TERMINAL = frozenset(
    {"succeeded", "failed", "timed_out", "oom_killed", "cancelled", "infra_error"}
)
ALL = frozenset({QUEUED, DISPATCHING, RUNNING}) | TERMINAL

_ALLOWED: dict[str, frozenset[str]] = {
    QUEUED: frozenset({DISPATCHING, "cancelled", "infra_error"}),
    DISPATCHING: frozenset({RUNNING, QUEUED, "cancelled", "infra_error"} | TERMINAL),
    RUNNING: TERMINAL,
}


def can_transition(src: str, dst: str) -> bool:
    return dst in _ALLOWED.get(src, frozenset())


def is_terminal(state: str) -> bool:
    return state in TERMINAL
