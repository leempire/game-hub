"""Deterministic chess-clock charging, flag fall and overtime behavior."""

from copy import deepcopy

import pytest

from app.game import chess_clock as clock


def test_unlimited_clock_is_disabled():
    assert clock.create(0, ("red", "black"), "red") is None
    assert clock.advance(None, 900000) is None
    assert clock.view(None) is None


def test_only_running_side_is_charged_and_exact_zero_loses():
    state = clock.create(300, ("red", "black"), "red")
    assert clock.advance(state, 1234) is None
    assert state["remaining_ms"] == {"red": 298766, "black": 300000}
    clock.complete_turn(state, "black")
    assert clock.advance(state, 300000) == "black"
    assert state["remaining_ms"]["red"] == 298766
    clock.complete_turn(state, "red")
    assert state["turn"] == "black"


def test_overtime_uses_only_remainder_and_resets_after_each_completed_turn():
    state = clock.create(1, ("red", "black"), "red", byoyomi_seconds=10)
    assert clock.advance(state, 1500) is None
    assert state["remaining_ms"]["red"] == 0
    assert state["period_left_ms"] == 9500
    clock.complete_turn(state, "black")
    assert state["period_left_ms"] == 10000
    assert clock.advance(state, 500) is None
    clock.complete_turn(state, "red")
    assert clock.advance(state, 9999) is None
    assert clock.advance(state, 1) == "red"


def test_main_time_exactly_zero_enters_full_overtime_period():
    state = clock.create(1, ("red", "black"), "red", byoyomi_seconds=10)
    assert clock.advance(state, 1000) is None
    assert state["period_left_ms"] == 10000
    assert clock.advance(state, 10001) == "red"


def test_expired_and_paused_clocks_do_not_change():
    state = clock.create(1, ("black", "white"), "black")
    state["paused"] = True
    before = deepcopy(state)
    assert clock.advance(state, 99999) is None
    assert state == before
    state["paused"] = False
    assert clock.advance(state, 1000) == "black"
    before = deepcopy(state)
    assert clock.advance(state, 99999) == "black"
    assert state == before


def test_public_view_does_not_share_mutable_time_map():
    state = clock.create(300, ("red", "black"), "red")
    public = clock.view(state)
    public["remaining_ms"]["red"] = 0
    assert state["remaining_ms"]["red"] == 300000


def test_invalid_time_inputs_rejected():
    with pytest.raises(ValueError):
        clock.advance(None, -1)
    with pytest.raises(ValueError):
        clock.create(-1, ("red", "black"), "red")
    with pytest.raises(ValueError):
        clock.create(1, ("red", "black"), "white")
