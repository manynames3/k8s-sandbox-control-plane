import pytest

from api import states


def test_happy_path():
    assert states.can_transition("queued", "dispatching")
    assert states.can_transition("dispatching", "running")
    assert states.can_transition("running", "succeeded")


@pytest.mark.parametrize("terminal", sorted(states.TERMINAL))
def test_terminal_states_are_final(terminal):
    for dst in states.ALL:
        assert not states.can_transition(terminal, dst)


def test_no_skipping_queue():
    assert not states.can_transition("queued", "running")
    assert not states.can_transition("queued", "succeeded")
