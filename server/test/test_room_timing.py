"""Deadline projections, fractional charging and persisted restart balances."""

import asyncio
from copy import deepcopy

import pytest

from app import database, persistence, room_timing


def room():
    return {"code": "CLOCK1", "game": "xiangqi", "status": "playing",
            "chess_clock_seconds": 300, "xiangqi_byoyomi_seconds": 10,
            "game_state": {"next": "red"}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(room_timing, "_anchors", {})
    monkeypatch.setattr(room_timing, "_tasks", {})
    monkeypatch.setattr(room_timing, "_on_deadline", None)


def test_projection_counts_elapsed_without_mutating_authority(monkeypatch):
    monkeypatch.setattr(room_timing, "_now", lambda: 100.0)
    state = room()
    room_timing.initialize(state)
    original = deepcopy(state["game_state"]["clock"])
    monkeypatch.setattr(room_timing, "_now", lambda: 100.125)
    view = room_timing.decorate(state, {})
    assert view["clock"]["remaining_ms"]["red"] == 299875
    assert state["game_state"]["clock"] == original
    room_timing.settle(state)
    assert state["game_state"]["clock"]["remaining_ms"]["red"] == 299875


def test_submillisecond_input_spam_does_not_stop_clock(monkeypatch):
    monkeypatch.setattr(room_timing, "_now", lambda: 100.0)
    state = room()
    room_timing.initialize(state)
    for now in (100.0004, 100.0008, 100.0012):
        room_timing.settle(state, now=now)
    assert state["game_state"]["clock"]["remaining_ms"]["red"] == 299999


def test_graceful_checkpoint_and_real_restore_exclude_service_downtime(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "clock.db"))
    database.init_db(database._db_path)
    monkeypatch.setattr(persistence, "_enabled", False)
    persistence.init()
    monkeypatch.setattr(room_timing, "_now", lambda: 100.0)
    state = room()
    room_timing.initialize(state)
    monkeypatch.setattr(room_timing, "_now", lambda: 101.125)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(room_timing.shutdown([state]))
    finally:
        loop.close()
    restored = persistence.load_all_rooms()[0]
    assert restored["game_state"]["clock"]["remaining_ms"]["red"] == 298875
    monkeypatch.setattr(room_timing, "_now", lambda: 90000.0)
    projected = room_timing.decorate(restored, {})["clock"]
    assert projected["remaining_ms"]["red"] == 298875
    assert projected["period_ms"] == 10000
    monkeypatch.setattr(room_timing, "_now", lambda: 90001.0)
    room_timing.settle(restored)
    assert restored["game_state"]["clock"]["remaining_ms"]["red"] == 297875
