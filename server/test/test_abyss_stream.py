"""Delta transport must reconstruct complete authoritative snapshots losslessly."""
import json

from app import abyss_stream
from app.game import abyss


def reconstruct(previous, delta):
    state = {**previous, **delta['game']}
    for key, patch in delta['rows'].items():
        rows = {abyss_stream.row_id(row): row for row in previous[key]}
        for uid in patch['remove']:
            rows.pop(uid)
        for row in patch['upsert']:
            rows[abyss_stream.row_id(row)] = row
        state[key] = list(rows.values())
    return state


def test_deltas_reconstruct_moving_combat_and_upgrade_pause_exactly():
    gs = abyss.create([{'id': 1, 'username': 'diver'}], 71)
    previous = abyss.snapshot(gs)
    for frame in range(500):
        if frame == 130:
            gs['xp'] = abyss.threshold(gs)
        abyss.move(gs, 1, 1 if frame < 200 else -1, .4)
        abyss.advance(gs)
        current = abyss.snapshot(gs)
        delta = json.loads(json.dumps(abyss_stream.encode(previous, current)))
        assert delta['base'] == previous['frame']
        assert reconstruct(previous, delta) == current
        previous = current


def test_static_high_density_effects_are_not_repeated_on_the_wire():
    gs = abyss.create([{'id': 1, 'username': 'diver'}], 71)
    for i in range(200):
        abyss._effect(gs, 'axe', 'impact', x=i, y=i, r=16, life=10)
    previous = abyss.snapshot(gs)
    gs['frame'] += 3
    current = abyss.snapshot(gs)
    delta = abyss_stream.encode(previous, current)
    assert 'effects' not in delta['rows']
    assert len(json.dumps(delta)) < len(json.dumps(current)) * .1


def test_removal_and_same_frame_changes_are_not_lost():
    gs = abyss.create([{'id': 1, 'username': 'diver'}], 71)
    abyss._effect(gs, 'rescue', 'rescue', x=10, y=20, r=54)
    previous = abyss.snapshot(gs)
    gs['effects'].clear()
    gs['players'][1]['hp'] = 42
    current = abyss.snapshot(gs)
    assert reconstruct(previous, abyss_stream.encode(previous, current)) == current
