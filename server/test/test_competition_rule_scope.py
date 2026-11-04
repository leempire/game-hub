"""Public ranking scopes ignore presentation and spectator preferences."""

from app.competition_rules import default_room, rule_scope
from app import room_settings


def test_cosmetic_animation_and_spectator_policy_do_not_split_gameplay_scopes():
    normal = default_room("ludo")
    changed = {**normal, "ludo_animation_speed": "fast"}
    assert rule_scope(normal)[0] == rule_scope(changed)[0]
    normal = default_room("uno")
    changed = {**normal, "allow_spectators": True}
    assert rule_scope(normal)[0] == rule_scope(changed)[0]


def test_new_default_rule_keeps_existing_scope_but_nondefault_value_separates(monkeypatch):
    room = default_room("gomoku")
    before = rule_scope(room)[0]
    extra = room_settings.Setting(key="future_rule", label="未来规则", kind="toggle",
                                  store="future_rule_enabled", default=False)
    monkeypatch.setitem(room_settings._GAMES, "gomoku", [*room_settings.specs_for("gomoku"), extra])
    assert rule_scope(room)[0] == before
    assert rule_scope({**room, "future_rule_enabled": False})[0] == before
    assert rule_scope({**room, "future_rule_enabled": True})[0] != before


def test_derived_registry_defaults_are_compared_using_the_read_function(monkeypatch):
    room = default_room("uno")
    before = rule_scope(room)[0]
    extra = room_settings.Setting(key="derived_rule", label="派生规则", kind="toggle",
                                  default=None, read=lambda state: state.get("derived_rule_enabled", True))
    monkeypatch.setitem(room_settings._GAMES, "uno", [*room_settings.specs_for("uno"), extra])
    assert rule_scope(room)[0] == before
    assert rule_scope({**room, "derived_rule_enabled": True})[0] == before
    assert rule_scope({**room, "derived_rule_enabled": False})[0] != before
