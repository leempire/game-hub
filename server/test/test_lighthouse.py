"""Economy pacing, offline settlement, and account-owned lighthouse API."""
import pytest
from fastapi.testclient import TestClient

from app import database
from app.game import lighthouse as game
from app.main import app
from app.routers import lighthouse as api
from app.security import get_current_user_id


def can_buy(state, action):
    offer = game.offers(state).get(action)
    return offer and all(state[key] >= offer[key] * game.SCALE for key in ("parts", "tickets")) and (
        state["charts"] >= offer["charts"]
    )


def test_offline_cap_fractional_precision_and_power_switch():
    state = game.new_save(0)
    game.apply_action(state, "start")
    game.settle(state, 59)
    assert state["parts"] == 354
    game.settle(state, 60)
    game.apply_action(state, "light")
    game.apply_action(state, "power_signal")
    assert game.rates(state) == {"parts": 0, "tickets": 1}

    summary = game.settle(state, 60 + 2 * game.OFFLINE_CAP)
    assert summary["seconds"] == game.OFFLINE_CAP
    assert state["tickets"] == game.OFFLINE_CAP
    assert state["parts"] == 0
    assert game.settle(state, 60)["seconds"] == 0
    assert game.settle(state, 60 + 2 * game.OFFLINE_CAP)["seconds"] == 0


def test_orders_rotate_and_arrival_awards_once():
    state = game.new_save(0)
    state.update(started_at=0, lit=True, battery=True, dock=True,
                 parts=100_000 * game.SCALE, tickets=100_000 * game.SCALE, completed=4)
    game.apply_action(state, "dispatch")
    game.apply_action(state, "order_parts")
    with pytest.raises(ValueError):
        game.apply_action(state, "order_tickets")
    game.settle(state, game.OFFLINE_CAP)
    game.apply_action(state, "order_tickets")
    arrived = game.settle(state, state["arrival_at"])
    assert arrived["arrived"] == game.ROUTES[4].name
    assert state["completed"] == 5
    assert state["charts"] == 3
    assert game.settle(state, state["updated_at"])["arrived"] is None


@pytest.mark.parametrize("hours, expect", [(1, (30, 33)), (12, (30, 36)), (24, (30, 40))])
def test_campaign_pacing(hours, expect):
    """Players take affordable upgrades and sail, then check at a fixed cadence."""
    state = game.new_save(0)
    game.apply_action(state, "start")
    first_departure = None
    onboarding = [60, 9 * 60, 69 * 60, 99 * 60, 249 * 60]
    for now in onboarding + list(range(6 * game.HOUR, 50 * 24 * game.HOUR, hours * game.HOUR)):
        game.settle(state, now)
        for action in ("light", "battery", "dock", "upgrade_signal", "upgrade_salvage", "dispatch"):
            if can_buy(state, action):
                game.apply_action(state, action)
                if action == "dispatch" and first_departure is None:
                    first_departure = now
        if state["completed_at"] is not None:
            break
    assert first_departure == 249 * 60
    assert sum(route.hours for route in game.ROUTES) == 30 * 24
    days = state["completed_at"] / (24 * game.HOUR)
    assert expect[0] <= days <= expect[1]


def test_authenticated_save_duplicate_request_and_cross_device_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_db_path", database._db_path)
    database.init_db(str(tmp_path / "lighthouse.db"))
    conn = database.get_conn()
    conn.executemany(
        "INSERT INTO users(id,username,password_hash,salt,created_at) VALUES (?,?,'h','s','2026-01-01')",
        [(1, "alice"), (2, "bob")],
    )
    conn.commit()
    conn.close()
    now = 100
    monkeypatch.setattr(api, "time", lambda: now)
    client = TestClient(app)
    assert client.get("/api/lighthouse").status_code == 401

    current_user = 1
    monkeypatch.setitem(app.dependency_overrides, get_current_user_id, lambda: current_user)
    initial = client.get("/api/lighthouse").json()
    command = {"action": "start", "operation_id": "example-start", "revision": initial["revision"]}
    endpoint = "/api/lighthouse/actions"
    started = client.post(endpoint, json=command).json()
    assert started["rates"]["parts"] == 6
    assert client.post(endpoint, json=command).json()["revision"] == started["revision"]
    now += 60
    stale = {"action": "light", "operation_id": "example-light", "revision": initial["revision"]}
    assert client.post(endpoint, json=stale).status_code == 409
    lit = client.post(endpoint, json={**stale, "revision": started["revision"]}).json()
    assert lit["lit"] and lit["parts"] == 0
    assert client.get("/api/lighthouse").json()["lit"]
    current_user = 2
    assert client.get("/api/lighthouse").json()["started_at"] is None


def test_port_modes_unlock_after_three_routes_and_settle_at_selected_rates():
    state = game.new_save(0)
    state.update(started_at=0, lit=True, battery=True, dock=True,
                 salvage_on=True, signal_on=True, signal_level=1, completed=2)
    assert "mode_parts" not in game.offers(state)
    state["completed"] = 3
    game.settle(state, 60)
    game.apply_action(state, "mode_parts")
    assert game.rates(state) == {"parts": 9, "tickets": 1}
    game.settle(state, 120)
    assert state["parts"] == 15 * game.SCALE
    assert state["tickets"] == 3 * game.SCALE
