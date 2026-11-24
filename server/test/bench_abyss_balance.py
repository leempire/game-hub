"""深渊回声固定步进平衡基准。仓库根运行 PYTHONPATH=server python3 server/test/bench_abyss_balance.py。

代理根据邻近敌人、墙距和掉落选择走位，能快速拾取经验。结果用于比较数值改动，
真人操作与沟通成本需要实战另测。
"""
import argparse
import math
from concurrent.futures import ProcessPoolExecutor
from app.game import abyss, abyss_data as data

DIRECTIONS = [(0, 0), *((math.cos(n * math.tau / 12), math.sin(n * math.tau / 12)) for n in range(12))]
GOOD = {'rivet': 12, 'arc': 9, 'ring': 10, 'charge': 8, 'whip': 6, 'spore': 8, 'bible': 9, 'harpoon': 5}
TICKS = 5

def choose_upgrade(p, choices):
    def score(item):
        if item in p['weapons']:
            return 12 + (8 if item == 'rivet' else GOOD.get(item, 3)) + p['weapons'][item]
        if item in p['passives']:
            return 11 if any(item == passive for _, passive in data.EVOLUTIONS.values()) else 7
        if item in data.WEAPONS:
            return 14 + GOOD.get(item, 3) if len(p['weapons']) < 3 else 5 + GOOD.get(item, 3)
        if item in data.PASSIVES:
            return 27 if any(data.WEAPONS[weapon].get('passive') == item for weapon in p['weapons']) else 10
        return 26 if item == 'repair' and p['hp'] < 36 else 0
    return max(choices, key=score)

def squared(p, x, y):
    return (p['x'] - x)**2 + (p['y'] - y)**2

def direction(gs, p):
    x, y = p['x'], p['y']
    width, height = abyss.MAP_SIZES[gs.get('map_size', 'medium')]
    danger = [enemy for enemy in gs['enemies'] if squared(enemy, x, y) < 250**2]
    nearby = sorted(gs['loot'], key=lambda item: squared(item, x, y))[:5]
    goals = [(item, 1 + .15 * min(6, item['xp'])) for item in nearby]
    goals.extend((chest, 2.1) for chest in gs['chests'])
    goals.extend((mate, 2.4) for mate in gs['players'].values() if not mate['hp'] and not mate['out'])
    if not goals:
        # Stay off the walls and let the starting weapon attack incoming enemies.
        goals.append(({'x': width / 2 if width else x, 'y': height / 2 if height else y}, .55))
    target, weight = min(goals, key=lambda item: squared(item[0], x, y) / item[1] ** 2)
    closest = math.sqrt(squared(target, x, y))
    distance = data.PLAYER_SPEED * p['stats']['move_speed'] * .28
    def score(vector):
        dx, dy = vector
        xx, yy = abyss._clamp(x + dx * distance, width), abyss._clamp(y + dy * distance, height)
        danger_cost = 0
        for enemy in danger:
            cur = math.sqrt(squared(enemy, x, y))
            gap = math.sqrt(squared(enemy, xx, yy))
            margin = enemy['radius'] + 15 + enemy['speed'] * .28
            if gap < margin + 30:
                danger_cost += max(0, margin + 30 - gap) ** 2 * .09
            if cur < margin and gap < cur:
                danger_cost += (cur - gap) * 3
        inset = abyss.WALL_INSET
        edge = min(xx - inset, yy - inset, width - xx - inset, height - yy - inset) if width else math.inf
        gain = closest - math.sqrt(squared(target, xx, yy))
        return gain * min(1.15, weight) - danger_cost - max(0, 94 - edge) * .85
    return max(DIRECTIONS, key=score)

def run_match(args):
    count, seed, minutes, pace, *map_sizes = args
    map_size = map_sizes[0] if map_sizes else "medium"
    gs = abyss.create(
        [{'id': uid, 'username': str(uid)} for uid in range(count)],
        seed, duration=minutes * 60, xp_pace=pace, map_size=map_size,
    )
    warden_defeated_at = None
    resolved_attacks = set()
    last_upgrade_at = 0
    level = gs['level']
    while not gs['result']:
        for p in gs['players'].values():
            if p['choices']:
                abyss.pick(gs, p['id'], gs['level'], choose_upgrade(p, p['choices']))
            if p['hp'] and gs['frame'] % TICKS == 0:
                abyss.move(gs, p['id'], *direction(gs, p))
        abyss.advance(gs)
        if gs['level'] != level:
            level = gs['level']
            last_upgrade_at = round(gs['elapsed'] * abyss.STEP)
        if gs['boss'] and not gs['boss_dead']:
            warden = next(enemy for enemy in gs['enemies'] if enemy['kind'] == 'warden')
            attack = warden.get('attack')
            if attack and attack['resolved']:
                resolved_attacks.add(attack['id'])
        if gs['boss_dead'] and warden_defeated_at is None:
            warden_defeated_at = round(gs['elapsed'] * abyss.STEP - gs['duration'] * 11 / 12, 1)
    return {
        'map_size': map_size, 'minutes': minutes, 'pace': pace, 'players': count, 'seed': seed, 'result': gs['result'],
        'last_upgrade_seconds': last_upgrade_at,
        'xp_remaining': gs['xp'], 'next_xp': abyss.threshold(gs),
        'weapon_slots': [len(p['weapons']) for p in gs['players'].values()],
        'evolutions': [sum(weapon in data.EVOLUTIONS for weapon in p['weapons']) for p in gs['players'].values()],
        'seconds': round(gs['elapsed'] * abyss.STEP), 'level': gs['level'], 'kills': gs['kills'],
        'warden_fight_seconds': warden_defeated_at, 'warden_attacks_resolved': len(resolved_attacks),
        'team_chests': len({event['frame'] for event in gs['chest_events']}),
        'hp': [round(p['hp']) for p in gs['players'].values()],
    }

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="深渊回声固定种子平衡基准")
    parser.add_argument("--minutes", nargs="+", type=int, choices=(12, 20, 30), default=[12])
    parser.add_argument("--pace", nargs="+", choices=("standard", "fast", "slow"), default=["standard"])
    parser.add_argument("--map-size", nargs="+", choices=abyss.MAP_SIZES, default=["medium"])
    args = parser.parse_args()
    paces = {"standard": 1, "fast": .8, "slow": 1.2}
    matches = [
        (count, seed, minutes, paces[pace], map_size)
        for map_size in args.map_size for minutes in args.minutes for pace in args.pace
        for count in range(1, 5) for seed in (11, 29, 47)
    ]
    with ProcessPoolExecutor(max_workers=4) as pool:
        for result in pool.map(run_match, matches):
            print(result, flush=True)
