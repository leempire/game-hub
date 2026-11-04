"""Skill tiers must reach engines, remain legal and ignore stale threaded results."""

import asyncio
import copy
import random
import threading

import pytest

from app import persistence, room_manager as rm, ws
from app.game import (ai_difficulty, gomoku, gomoku_ai, reversi, reversi_ai,
                      connect4, connect4_ai, xiangqi, xiangqi_ai, dots, dots_ai,
                      uno, uno_ai, stasis_ai)


GAMES = ('gomoku', 'reversi', 'battleship', 'dots', 'ludo', 'xiangqi',
         'connect4', 'uno', 'stasis', 'luzhanqi', 'uttt')


@pytest.fixture
def isolated_rooms(monkeypatch):
    monkeypatch.setattr(rm, '_rooms', {})
    monkeypatch.setattr(persistence, '_enabled', False)


@pytest.mark.parametrize('game', GAMES)
@pytest.mark.parametrize('level', ['easy', 'normal', 'hard', None, [], 'invalid'])
def test_all_rooms_store_and_sync_tier(isolated_rooms, game, level):
    room = rm.create_room({'id': 1, 'username': 'host', 'nickname': None}, None, None, game)
    rm.add_bot(room['code'], 1, level)
    bot = room['players'][1]
    assert bot['ai_difficulty'] == ai_difficulty.normalize_difficulty(level)
    assert ws._bot_difficulty(room, bot['id']) == bot['ai_difficulty']
    assert rm.room_to_dict(room)['players'][1]['ai_difficulty'] == bot['ai_difficulty']


@pytest.mark.parametrize('level', ['easy', 'normal', 'hard'])
@pytest.mark.parametrize('game,ai,player', [
    (gomoku, gomoku_ai, 2), (reversi, reversi_ai, 1),
    (connect4, connect4_ai, 1), (xiangqi, xiangqi_ai, 'red'),
])
def test_board_tiers_are_legal_and_do_not_mutate(game, ai, player, level):
    board = game.create_board()
    if game is gomoku:
        board[7][7] = 1
    before = copy.deepcopy(board)
    move = ai.best_move(board, player, difficulty=level, rng=random.Random(7))
    assert board == before
    if game is gomoku:
        assert game.is_valid_move(board, *move)
    elif game is reversi:
        assert move in game.get_legal_moves(board, player)
    elif game is connect4:
        assert move in game.legal_columns(board)
    else:
        assert move in game.all_legal_moves(board, player)


def test_uno_hard_uses_public_counts_to_block_and_never_hidden_identity():
    gs = uno.create_game([1, 2, 3])
    gs['hands'] = {0: ['R1', 'RS', 'B4'], 1: ['G3'], 2: ['Y4', 'G5']}
    gs['current_color'] = 'R'
    gs['discard_pile'] = ['R5']
    assert uno_ai.best_action(gs, 0)['card'] == 'R1'
    assert uno_ai.best_action(gs, 0, 'hard')['card'] == 'RS'
    gs['hands'][1] = ['B9']
    gs['draw_pile'].reverse()
    assert uno_ai.best_action(gs, 0, 'hard')['card'] == 'RS'


def test_stasis_tiers_preserve_personality_and_normal_profile():
    profile = stasis_ai.PROFILES['medic']
    easy = stasis_ai.skill_profile(profile, 'easy')
    normal = stasis_ai.skill_profile(profile)
    hard = stasis_ai.skill_profile(profile, 'hard')
    assert normal is profile
    assert easy.skill < normal.skill <= hard.skill
    assert easy.noise > normal.noise > hard.noise
    assert easy.ally_ratio == normal.ally_ratio == hard.ally_ratio


@pytest.mark.parametrize('callback,module,next_player', [
    (ws._bot_gomoku_move, gomoku_ai, 'black'),
    (ws._bot_reversi_move, reversi_ai, 'black'),
    (ws._bot_connect4_move, connect4_ai, 'black'),
    (ws._bot_xiangqi_move, xiangqi_ai, 'red'),
    (ws._bot_dots_move, dots_ai, 1),
])
def test_threaded_search_receives_tier_and_drops_replaced_game(
        isolated_rooms, monkeypatch, callback, module, next_player):
    started, release = threading.Event(), threading.Event()
    board = reversi.create_board()
    gs = {'board': board, 'moves': [], 'next': next_player, 'player_colors': {-1: 'black' if module is dots_ai else next_player}}
    room = {'status': 'playing', 'game_state': gs,
            'players': [{'id': -1, 'ai_difficulty': 'hard'}]}
    rm._rooms['BOT123'] = room
    captured = []

    def search(state, *args, difficulty=None):
        captured.append(difficulty or args[-1])
        assert state is not gs and state is not board
        started.set()
        assert release.wait(3)
        return (0, 0)

    monkeypatch.setattr(module, 'best_move', search)

    async def run():
        task = asyncio.create_task(callback('BOT123', -1, room, gs))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            room['game_state'] = dict(gs, moves=[])
        finally:
            release.set()
        await task

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(run())
    finally:
        loop.close()
    assert captured == ['hard']
    assert gs['moves'] == []


@pytest.mark.parametrize('level', ['easy', 'normal', 'hard'])
def test_dots_exact_endgame_matches_full_game_oracle(level):
    gs = dots.create_game(dots=3)
    # A complete tiny board is small enough for an independent engine-based oracle.
    for edge in [('h', 0, 0), ('h', 0, 1), ('v', 0, 0), ('v', 0, 2)]:
        dots.apply_move(gs, *edge)
    before = copy.deepcopy(gs)
    move = dots_ai.best_move(gs, level, rng=random.Random(12))
    assert gs == before and move in dots.get_legal_edges(gs)
    if level != 'hard':
        return

    def score(state, root):
        legal = dots.get_legal_edges(state)
        if not legal:
            other = 2 if root == 1 else 1
            return state['scores'][root] - state['scores'][other]
        results = []
        for edge in legal:
            child = copy.deepcopy(state)
            dots.apply_move(child, *edge)
            results.append(score(child, root))
        return (max if state['next'] == root else min)(results)

    root = gs['next']
    values = []
    for edge in dots.get_legal_edges(gs):
        child = copy.deepcopy(gs)
        dots.apply_move(child, *edge)
        values.append((score(child, root), edge))
    assert next(v for v, edge in values if edge == move) == max(v for v, _ in values)
