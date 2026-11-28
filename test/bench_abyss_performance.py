"""Late-game six-evolution stress bench: PYTHONPATH=server python test/bench_abyss_performance.py."""
import json
import math
import statistics
import time

from app import abyss_stream
from app.game import abyss


def crowded_game(count):
    gs = abyss.create([{'id': i, 'username': str(i)} for i in range(count)], 41)
    gs.update(elapsed=11000, frame=11000, spawn_budget=-100000, level=80)
    for p in gs['players'].values():
        p['weapons'] = dict.fromkeys(('holy_wand', 'thousand_edge', 'death_spiral',
                                     'hellfire', 'no_future', 'unholy_vespers'), 1)
        p['passives'] = dict(empty_tome=5, duplicator=2, bracer=5,
                             candelabrador=5, spellbinder=5, spinach=5)
        p['invincible'] = 1000000
        abyss._refresh_stats(p)
    p = gs['players'][0]
    for i in range(240):
        abyss._spawn(gs, 'drifter', p)
        angle = i * math.tau / 240
        enemy = gs['enemies'][-1]
        enemy.update(x=p['x'] + math.cos(angle) * (90 + i % 9 * 28),
                     y=p['y'] + math.sin(angle) * (90 + i % 9 * 28),
                     hp=10000000, max_hp=10000000, speed=0)
    return gs


def run(count):
    gs = crowded_game(count)
    simulation, snapshots, sizes, deltas = [], [], [], []
    previous = None
    for tick in range(500):
        started = time.perf_counter()
        abyss.advance(gs)
        elapsed = (time.perf_counter() - started) * 1000
        if tick >= 100:
            simulation.append(elapsed)
        if tick >= 100 and tick % 3 == 0:
            started = time.perf_counter()
            wire = json.dumps(abyss.snapshot(gs), separators=(',', ':'))
            snapshots.append((time.perf_counter() - started) * 1000)
            sizes.append(len(wire))
            current = abyss.snapshot(gs)
            if previous is not None:
                deltas.append(len(json.dumps(abyss_stream.encode(previous, current), separators=(',', ':'))))
            previous = current
    return {'players': count, 'tick_median_ms': round(statistics.median(simulation), 3),
            'tick_p95_ms': round(sorted(simulation)[int(len(simulation) * .95)], 3),
            'snapshot_median_ms': round(statistics.median(snapshots), 3),
            'wire_peak_bytes': max(sizes), 'delta_median_bytes': round(statistics.median(deltas)),
            'full_median_bytes': round(statistics.median(sizes)), 'shots': len(gs['shots']), 'effects': len(gs['effects'])}


if __name__ == '__main__':
    for count in (1, 4):
        print(json.dumps(run(count)))
