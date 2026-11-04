"""The optional public history prevents immediate root-level long-check forfeits."""
from copy import deepcopy

import pytest
from app.game import xiangqi as xq, xiangqi_ai as ai


def repeated_check_position():
    gs = xq.create_game()
    gs['board'] = [[None] * 9 for _ in range(10)]
    gs['board'][0][4] = 'k'
    gs['board'][9][3] = 'K'
    gs['board'][1][4] = 'R'
    gs['next'] = 'black'
    cycle = [((0, 4), (0, 5)), ((1, 4), (1, 5)),
             ((0, 5), (0, 4)), ((1, 5), (1, 4))]
    for move in cycle + cycle[:3]:
        result = xq.make_move(gs, *move)
        assert result['ok'] and not result['game_over']
    return gs


@pytest.mark.parametrize('difficulty', ['easy', 'normal', 'hard'])
def test_public_history_filters_immediate_long_check_loss_without_mutation(difficulty):
    gs = repeated_check_position()
    losing = ((1, 5), (1, 4))
    trial = deepcopy(gs)
    assert xq.make_move(trial, *losing)['game_over']
    assert (trial['winner'], trial['reason']) == ('black', 'perpetual_check')
    before = deepcopy(gs)
    move = ai.best_move(gs['board'], 'red', time_budget=0, difficulty=difficulty, game=gs)
    assert move != losing
    assert gs == before
    result = xq.make_move(trial := deepcopy(gs), *move)
    assert result['ok']
    assert not (trial['winner'] == 'black' and trial['reason'] == 'perpetual_check')


def test_filter_retains_legal_fallback_when_every_candidate_loses():
    gs = repeated_check_position()
    losing = ((1, 5), (1, 4))
    assert ai._avoid_immediate_perpetual_loss([losing], 'red', gs) == [losing]


def test_board_only_call_remains_supported():
    gs = repeated_check_position()
    move = ai.best_move(gs['board'], 'red', time_budget=0)
    assert move in xq.all_legal_moves(gs['board'], 'red')


def test_unique_history_skips_trial_copies_and_engine_calls(monkeypatch):
    moves = [((9, 0), (8, 0))]
    def unexpected(*args):
        raise AssertionError('unique history cannot immediately reach a third repetition')
    monkeypatch.setattr(ai, 'deepcopy', unexpected)
    monkeypatch.setattr(ai, 'make_move', unexpected)
    assert ai._avoid_immediate_perpetual_loss(moves, 'red', {
        '_repetition_positions': ['first', 'second', 'third'],
    }) is moves
