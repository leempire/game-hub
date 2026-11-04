"""Measure skill tiers on repeatable tactical cases and complete seeded games.

Run from server/: python test/bench_ai_difficulty.py --games 8
Tactical cases measure a narrow ability, not an Elo estimate. Stasis measures
cooperative survival with identical seeds and monster settings across skill tiers.
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.game import (gomoku, gomoku_ai, connect4, connect4_ai, reversi, reversi_ai,
                      xiangqi, xiangqi_ai, dots, dots_ai, uno, uno_ai, ludo, ludo_ai,
                      luzhanqi as military, luzhanqi_ai)
from bench_luzhanqi_ai import _new_game
from balance_stasis import play as stasis_play

LEVELS = ('easy', 'normal', 'hard')


def tactical(level, samples):
    wins = dict.fromkeys(('gomoku', 'connect4', 'reversi', 'xiangqi', 'luzhanqi'), 0)
    elapsed = dict.fromkeys(wins, 0.0)
    for seed in range(samples):
        board = gomoku.create_board()
        for x in range(4):
            board[5][x] = 2
        cases = [('gomoku', gomoku_ai, board, 2, lambda m: m == (4, 5))]
        board = connect4.create_board()
        board[5][:3] = [1, 1, 1]
        cases.append(('connect4', connect4_ai, board, 1, lambda m: m == 3))
        board = reversi.create_board()
        board[0][:3] = [0, 2, 1]
        cases.append(('reversi', reversi_ai, board, 1, lambda m: m == (0, 0)))
        board = [[None] * 9 for _ in range(10)]
        board[9][4], board[0][4], board[5][4] = 'K', 'k', 'P'
        board[1][4], board[1][0] = 'R', 'R'
        cases.append(('xiangqi', xiangqi_ai, board, 'red', lambda m: m[1] == (0, 4)))
        for name, ai, board, player, good in cases:
            start = time.monotonic()
            move = ai.best_move(board, player, difficulty=level, rng=random.Random(seed))
            wins[name] += int(good(move))
            elapsed[name] += time.monotonic() - start
        gs = _new_game('open', True, seed)
        board = gs['board']
        for row in board:
            row[:] = [None] * len(row)
        board[11][1] = {'c': military.BLACK, 'p': military.JUNQI}
        board[10][1] = {'c': military.RED, 'p': military.PAIZHANG}
        board[11][3] = {'c': military.RED, 'p': military.JUNQI}
        board[0][0] = {'c': military.BLACK, 'p': military.PAIZHANG}
        gs['next'] = military.RED
        start = time.monotonic()
        move = luzhanqi_ai.best_move(military.view_for(gs, military.RED), difficulty=level,
                                    rng=random.Random(seed))
        wins['luzhanqi'] += int(move == ((10, 1), (11, 1)))
        elapsed['luzhanqi'] += time.monotonic() - start
    return {g: {'tactical_success': wins[g], 'cases': samples,
                'mean_ms': round(elapsed[g] * 1000 / samples, 2)} for g in wins}


def dots_match(level, seed):
    gs = dots.create_game()
    side = 1 + seed % 2
    rng = random.Random(seed)
    while not gs['winner']:
        move = dots_ai.best_move(gs, level if gs['next'] == side else 'easy', rng)
        dots.apply_move(gs, *move)
    other = 3 - side
    return gs['scores'][side] - gs['scores'][other]


def ludo_match(level, seed):
    gs = ludo.create_game(2)
    side, rng = seed % 2, random.Random(seed)
    for _ in range(20000):
        if gs['finished']:
            return ludo.compute_ranking(gs, [{'seat': i, 'username': str(i)} for i in range(2)])[0]['seat'] == side
        ludo.roll_dice(gs, rng.randint(1, 6))
        if gs['phase'] == 'move' and not gs['finished']:
            move = ludo_ai.best_move(gs, level if gs['current_seat'] == side else 'easy', rng)
            ludo.move_piece(gs, move)
    raise AssertionError('Ludo did not terminate')


def uno_match(level, seed):
    random.seed(seed)
    gs = uno.create_game([1, 2])
    side, rng = seed % 2, random.Random(seed)
    for _ in range(20000):
        if gs['finished']:
            return gs['winner'] == side
        seat = gs['turn']
        act = uno_ai.best_action(gs, seat, level if seat == side else 'easy', rng)
        kind = act['action']
        if kind == 'play':
            uno.play(gs, seat, act['card'], act.get('color'), target=act.get('target'))
        elif kind == 'draw':
            uno.draw(gs, seat)
        elif kind == 'roulette':
            uno.resolve_roulette(gs, seat, act['color'])
        else:
            uno.pass_turn(gs, seat)
    raise AssertionError('UNO did not terminate')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--games', type=int, default=8)
    args = parser.parse_args()
    for level in LEVELS:
        results = tactical(level, args.games)
        results['dots'] = {'score_margin_vs_easy': sum(dots_match(level, i) for i in range(args.games)),
                           'games': args.games}
        for name, match in [('ludo', ludo_match), ('uno', uno_match)]:
            results[name] = {'wins_vs_easy': sum(match(level, i) for i in range(args.games)),
                             'games': args.games}
        survival = [stasis_play(('ai-turtle', 'ai-casual', 'ai-miner')[i % 3], n, 20261013 + i,
                                ai_difficulty=level, strict=True)
                    for n in (2, 4, 6) for i in range(args.games)]
        results['stasis'] = {'survivors': sum(x['alive'] for x in survival),
                             'players': args.games * 12, 'games': args.games * 3,
                             'mean_seconds': round(sum(x['ms'] for x in survival) / len(survival) / 1000, 1)}
        print(json.dumps({'difficulty': level, 'results': results}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
