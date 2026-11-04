"""Official +4 arbitration, event-window UNO catches and idempotent 500-point series."""

import copy
import random

import pytest

from app.game import uno as u, uno_official, uno_series, uno_ai


def setup(hands=None, **rules):
    gs = u.create_game([101, 102, 103], rules)
    gs['hands'] = hands or {0: ['W+4', 'R2'], 1: ['G3', 'B4'], 2: ['Y1', 'G5']}
    gs['discard_pile'], gs['current_color'] = ['R5'], 'R'
    gs['draw_pile'] = ['B2'] * 30
    return gs


def test_strict_draw_four_uses_color_before_wild_not_matching_symbol():
    gs = setup(wild4_strict=True)
    assert 'W+4' not in u.playable_cards(gs, 0)
    before = copy.deepcopy(gs)
    assert not u.play(gs, 0, 'W+4', 'G')['ok']
    assert gs == before
    gs['hands'][0] = ['W+4', 'B5']  # matching number is allowed
    assert 'W+4' in u.playable_cards(gs, 0)


@pytest.mark.parametrize('guilty', [True, False])
def test_challenge_success_and_failure_never_ship_private_evidence(guilty):
    gs = setup(wild4_challenge=True)
    if not guilty:
        gs['hands'][0][1] = 'B2'
    assert u.play(gs, 0, 'W+4', 'G')['ok']
    view = u.game_to_sync(gs, 102)
    assert view['can_challenge_wild4']
    assert 'guilty' not in repr(view) and 'wild4_pending' not in view
    assert not u.game_to_sync(gs, 101)['can_challenge_wild4']
    before = copy.deepcopy(gs)
    assert not u.challenge_draw4(gs, 2)['ok']
    assert gs == before
    result = u.challenge_draw4(gs, 1)
    assert result['ok'] and result['success'] == guilty
    assert result['penalty'] == (4 if guilty else 6)
    assert gs['turn'] == (1 if guilty else 2)
    assert gs['pending_draw'] == 0
    assert not u.challenge_draw4(gs, 1)['ok']


def test_challenge_window_closes_after_accepting_or_stacking():
    gs = setup(wild4_challenge=True)
    u.play(gs, 0, 'W+4', 'G')
    assert u.draw(gs, 1)['ok']
    assert not u.challenge_draw4(gs, 1)['ok']
    gs = setup(wild4_challenge=True)
    gs['hands'][1] = ['W+4', 'G2']
    u.play(gs, 0, 'W+4', 'G')
    u.play(gs, 1, 'W+4', 'B')
    assert not u.game_to_sync(gs, 103)['can_challenge_wild4']


def test_uno_catch_window_and_calling_race():
    gs = setup(hands={0: ['R1', 'B1'], 1: ['R2', 'G3'], 2: ['B5']}, uno_penalty=True)
    u.play(gs, 0, 'R1')
    assert u.game_to_sync(gs, 102)['catch_uno_targets'] == [0]
    # Old one-card hands are not reopened by an unrelated action.
    assert 2 not in uno_official.catch_targets(gs, 1)
    assert u.call_uno(gs, 0)['ok']
    assert not u.catch_uno(gs, 1, 0)['ok']
    gs = setup(hands={0: ['R1', 'B1'], 1: ['R2', 'G3'], 2: ['B5']}, uno_penalty=True)
    u.play(gs, 0, 'R1')
    assert u.catch_uno(gs, 1, 0)['penalty'] == 2
    assert not u.catch_uno(gs, 2, 0)['ok']
    gs = setup(hands={0: ['R1', 'B1'], 1: ['R2', 'G3'], 2: ['B5']}, uno_penalty=True)
    u.play(gs, 0, 'R1')
    u.play(gs, 1, 'R2')
    assert not u.catch_uno(gs, 2, 0)['ok']
    assert u.catch_uno(gs, 2, 1)['ok']


def test_final_penalty_is_included_in_series_and_round_settlement_is_idempotent():
    gs = setup(hands={0: ['W+4'], 1: ['G3'], 2: ['Y1']}, score_target=500)
    u.play(gs, 0, 'W+4', 'G')
    assert gs['finished'] and len(gs['hands'][1]) == 5
    series = uno_series.create_series([101, 102, 103])
    total = sum(u.hand_points(hand) for seat, hand in gs['hands'].items() if seat != 0)
    result = uno_series.add_round(series, gs, 'round-1')
    assert result['scores']['101'] == total
    assert series['scores']['101'] == 0
    assert uno_series.add_round(result, gs, 'round-1') == result
    for i in range(50):
        if result['finished']:
            break
        result = uno_series.add_round(result, gs, f'round-{i + 2}')
    assert result['finished'] and result['winners'] == [101]
    with pytest.raises(ValueError):
        uno_series.add_round(result, gs, 'new-round')


def test_series_rejects_changed_roster_and_surrenders_do_not_score():
    gs = setup()
    gs['finished'], gs['winner'], gs['reason'] = True, 0, 'last_remaining'
    series = uno_series.create_series([101, 102, 103])
    result = uno_series.add_round(series, gs, 1)
    assert result['scores'] == series['scores']
    gs['seat_uids'] = [102, 101, 103]
    with pytest.raises(ValueError):
        uno_series.add_round(series, gs, 2)


@pytest.mark.parametrize('rules', [
    {'wild4_strict': True},
    {'wild4_challenge': True, 'uno_penalty': True, 'stack_mode': 'off'},
    {'wild4_strict': True, 'wild4_challenge': True, 'uno_penalty': True,
     'draw_until_play': True, 'must_play': True, 'score_target': 500},
])
def test_official_switch_combinations_complete_legal_ai_games(rules):
    for seed in range(6):
        random.seed(8000 + seed)
        gs = u.create_game([101, 102, 103], rules)
        rng = random.Random(seed)
        for _ in range(10000):
            if gs['finished']:
                break
            seat = gs['turn']
            act = uno_ai.best_action(gs, seat, 'hard', rng)
            if act['action'] == 'play':
                result = u.play(gs, seat, act['card'], act.get('color'), target=act.get('target'))
            elif act['action'] == 'draw':
                result = u.draw(gs, seat)
            elif act['action'] == 'roulette':
                result = u.resolve_roulette(gs, seat, act['color'])
            else:
                result = u.pass_turn(gs, seat)
            assert result['ok'], (act, result)
        assert gs['finished']
