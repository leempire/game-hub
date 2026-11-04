"""Variable board geometry, Renju forbidden points and PopOut authoritative actions."""

import copy
import random

import pytest

from app import room_settings
from app.game import gomoku as g, gomoku_ai, reversi as r, reversi_ai
from app.game import connect4 as c, connect4_ai, dots, dots_ai


@pytest.mark.parametrize('size', [9, 13, 15, 19])
def test_gomoku_sizes_and_edge_win(size):
    board = g.create_board(size)
    for x in range(size - 5, size):
        board[size - 1][x] = g.WHITE
    assert g.check_win(board, size - 1, size - 1)
    assert not g.is_valid_move(board, size, 0)
    assert g.is_board_full(size * size, size)
    assert gomoku_ai.best_move(g.create_board(size), g.BLACK) == (size // 2, size // 2)


@pytest.mark.parametrize('size', [6, 8, 10])
def test_reversi_sizes_moves_and_ai(size):
    board = r.create_board(size)
    assert r.count_stones(board) == {'black': 2, 'white': 2}
    assert len(r.get_legal_moves(board, r.BLACK)) == 4
    for level in ('easy', 'normal', 'hard'):
        before = copy.deepcopy(board)
        move = reversi_ai.best_move(board, r.BLACK, level)
        assert move in r.get_legal_moves(board, r.BLACK)
        assert board == before
    # Bottom-right corner must use this board's bounds, not the old 8x8 constant.
    board[size - 1][size - 3:size] = [r.BLACK, r.WHITE, r.EMPTY]
    assert r.is_valid_move(board, size - 1, size - 1, r.BLACK)


@pytest.mark.parametrize('size', [4, 5, 6])
def test_dots_sizes_whole_game(size):
    gs = dots.create_game(size)
    rng = random.Random(size)
    while not gs['winner']:
        dots.apply_move(gs, *dots_ai.best_move(gs, 'hard', rng))
    assert sum(gs['scores'].values()) == (size - 1) ** 2
    assert len(gs['moves']) == 2 * size * (size - 1)


def _cross(horizontal, vertical):
    board = g.create_board()
    for x in horizontal:
        board[7][x] = g.BLACK
    for y in vertical:
        board[y][7] = g.BLACK
    return board


def test_renju_double_three_and_broken_three():
    for horizontal in ([6, 8], [6, 9]):
        board = _cross(horizontal, [6, 8])
        before = copy.deepcopy(board)
        assert g.forbidden_reason(board, 7, 7) == 'double_three'
        assert board == before
        assert (7, 7) not in g.legal_moves(board, g.BLACK, forbidden=True)
        assert (7, 7) in g.legal_moves(board, g.WHITE, forbidden=True)


def test_renju_blocked_three_is_not_a_true_three():
    board = _cross([6, 8], [6, 8])
    board[7][5] = board[7][9] = g.WHITE
    assert g.forbidden_reason(board, 7, 7) is None


def test_renju_double_four_overline_and_exact_five_precedence():
    board = _cross([4, 5, 6], [4, 5, 6])
    assert g.forbidden_reason(board, 7, 7) == 'double_four'
    board = _cross([2, 3, 4, 5, 6], [])
    assert g.forbidden_reason(board, 7, 7) == 'overline'
    for y in (3, 4, 5, 6):
        board[y][7] = g.BLACK
    assert g.forbidden_reason(board, 7, 7) is None
    board[7][7] = g.BLACK
    assert g.check_win(board, 7, 7, forbidden=True)


def _state(board=None, popout=True):
    return {'board': board or c.create_board(), 'moves': [], 'next': 'black',
            'winner': None, 'reason': None, 'popout': popout}


def test_pop_rejects_opponent_bottom_and_disabled_variant_without_mutation():
    gs = _state()
    gs['board'][5][0] = c.WHITE
    before = copy.deepcopy(gs)
    assert not c.apply_action(gs, c.BLACK, 0, 'pop')['ok']
    assert gs == before
    gs['board'][5][0] = c.BLACK
    gs['popout'] = False
    before = copy.deepcopy(gs)
    assert not c.apply_action(gs, c.BLACK, 0, 'pop')['ok']
    assert gs == before


def _double_line_board():
    board = c.create_board()
    for col in (0, 1, 2):
        board[5][col], board[4][col] = c.WHITE, c.BLACK
    board[5][3], board[4][3], board[3][3] = c.BLACK, c.WHITE, c.BLACK
    return board


def test_pop_simultaneous_lines_awards_mover_and_ai_uses_pop():
    gs = _state(_double_line_board())
    assert connect4_ai.best_action(gs['board'], c.BLACK, True, 'hard') == {'action': 'pop', 'col': 3}
    result = c.apply_action(gs, c.BLACK, 3, 'pop')
    assert result['ok'] and gs['winner'] == 'black'
    assert c.winning_players(gs['board']) == {c.BLACK, c.WHITE}
    assert result['move']['board'] == gs['board']


def test_pop_can_award_opponent_and_threefold_repetition_draws():
    board = _double_line_board()
    board[3][3] = c.EMPTY
    gs = _state(board)
    c.apply_action(gs, c.BLACK, 3, 'pop')
    assert gs['winner'] == 'white'
    gs = _state()
    for _ in range(2):
        for player, col, action in ((c.BLACK, 0, 'drop'), (c.WHITE, 1, 'drop'),
                                    (c.BLACK, 0, 'pop'), (c.WHITE, 1, 'pop')):
            assert c.apply_action(gs, player, col, action)['ok']
    assert gs['winner'] == 'draw' and gs['reason'] == 'repetition'


def test_variants_are_registered_as_room_settings():
    for game, key, expected in [('dots', 'dots', 6), ('reversi', 'board_size', 10),
                                ('gomoku', 'board_size', 19), ('connect4', 'popout', True)]:
        spec = room_settings.spec_for(game, key)
        room = {}
        room_settings.apply(room, spec, room_settings.coerce(spec, expected))
        assert room_settings.value_of(spec, room) == expected


def test_near_full_forbidden_board_has_no_black_move_without_existing_five():
    board = [[1 if (x + 2 * y) % 4 < 2 else 2 for x in range(9)] for y in range(9)]
    for x in (1, 2, 3, 5, 6, 7):
        board[4][x] = 1
    board[4][4] = 0
    assert not any(g.check_win(board, x, y) for y in range(9) for x in range(9) if board[y][x])
    assert g.forbidden_reason(board, 4, 4) == 'overline'
    assert g.legal_moves(board, g.BLACK, True) == []
    assert g.legal_moves(board, g.WHITE, True) == [(4, 4)]


def test_xiangqi_byoyomi_requires_main_time_and_disabling_clock_clears_it():
    room = {'game': 'xiangqi', 'chess_clock_seconds': 0, 'xiangqi_byoyomi_seconds': 0}
    byoyomi = room_settings.spec_for('xiangqi', 'byoyomi_seconds')
    clock = room_settings.spec_for('xiangqi', 'clock_seconds')
    before = copy.deepcopy(room)
    with pytest.raises(ValueError, match='先选择棋钟'):
        room_settings.apply(room, byoyomi, 10)
    assert room == before
    room_settings.apply(room, clock, 300)
    room_settings.apply(room, byoyomi, 30)
    assert room['xiangqi_byoyomi_seconds'] == 30
    room_settings.apply(room, clock, 0)
    assert room['xiangqi_byoyomi_seconds'] == 0
