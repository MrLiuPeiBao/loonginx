from __future__ import annotations

from client.utils.degraded_state import update_degraded_state


def test_enter_degraded_after_threshold() -> None:
    degraded, disconnected_since, connected_since = update_degraded_state(
        connected=False,
        now=100.0,
        degraded=False,
        disconnected_since=None,
        connected_since=None,
        enter_after=60.0,
        exit_after=10.0,
    )
    assert degraded is False
    assert disconnected_since == 100.0

    degraded, disconnected_since, connected_since = update_degraded_state(
        connected=False,
        now=161.0,
        degraded=degraded,
        disconnected_since=disconnected_since,
        connected_since=connected_since,
        enter_after=60.0,
        exit_after=10.0,
    )
    assert degraded is True


def test_exit_degraded_after_stable_connect() -> None:
    degraded, disconnected_since, connected_since = update_degraded_state(
        connected=True,
        now=200.0,
        degraded=True,
        disconnected_since=120.0,
        connected_since=None,
        enter_after=60.0,
        exit_after=10.0,
    )
    assert degraded is True
    assert disconnected_since is None
    assert connected_since == 200.0

    degraded, disconnected_since, connected_since = update_degraded_state(
        connected=True,
        now=211.0,
        degraded=degraded,
        disconnected_since=disconnected_since,
        connected_since=connected_since,
        enter_after=60.0,
        exit_after=10.0,
    )
    assert degraded is False
