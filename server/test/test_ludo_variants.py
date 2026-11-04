"""Aeroplane team ownership and rule switches retain authoritative paths."""

import random

import pytest

from app.game import ludo as l, ludo_ai


def moving(players=2, **rules):
    gs = l.create_game(players, **rules)
    gs['phase'], gs['dice_value'], gs['movable_pieces'] = 'move', 3, [0]
    return gs


def test_team_requires_four_and_ships_membership():
    with pytest.raises(ValueError):
        l.create_game(3, teams='2v2')
    gs = l.create_game(4, teams='2v2')
    assert l.game_to_sync(gs)['seat_teams'] == [0, 1, 0, 1]


def test_team_members_do_not_capture_or_block():
    gs = moving(4, teams='2v2')
    gs['players'][0]['pieces'][0] = 2
    # Seat2 progress31 occupies global5, the mover's destination.
    gs['players'][2]['pieces'] = [31, 31, -1, -1]
    assert not l.enemy_stacks_at(gs, 0, 5)
    result = l.move_piece(gs, 0)
    assert result['new_progress'] == 5 and result['captures'] == []
    assert gs['players'][2]['pieces'] == [31, 31, -1, -1]


def test_team_needs_both_members_finished_and_surrender_loses_whole_team():
    gs = l.create_game(4, teams='2v2')
    for seat in (0, 2):
        gs['players'][seat]['finished_count'] = 4
        gs['players'][seat]['finished'] = True
    assert l._check_game_over(gs)['winner_team'] == 0
    ranking = l.compute_ranking(gs, [{'seat': s, 'username': str(s)} for s in range(4)])
    assert [(entry['seat'], entry['rank']) for entry in ranking] == [(0, 1), (2, 1), (1, 2), (3, 2)]
    gs = l.create_game(4, teams='2v2')
    assert l.surrender(gs, 0)['winner_team'] == 1
    assert gs['finished']


def test_capture_off_disables_collisions_and_enemy_stack_obstruction():
    gs = moving(capture=False)
    gs['players'][0]['pieces'][0] = 2
    gs['players'][1]['pieces'] = [44, 44, -1, -1]
    assert not l.enemy_stacks_at(gs, 0, 5)
    result = l.move_piece(gs, 0)
    assert result['captures'] == [] and result['new_progress'] == 5
    assert gs['players'][1]['pieces'] == [44, 44, -1, -1]


def test_color_jump_off_keeps_dice_path_and_existing_flight():
    gs = moving(color_jump=False)
    gs['players'][0]['pieces'][0] = 1
    result = l.move_piece(gs, 0)
    assert result['path'] == [2, 3, 4] and result['new_progress'] == 4
    assert result['segments'] == []
    # Shortcut remains distinct from same-color +4 jumping.
    assert l.resolve_landing(gs, 0, 16)[0] == 28


def test_non_exact_finish_clamps_path_and_ai_uses_same_destination():
    gs = moving(exact_finish=False)
    gs['players'][0]['pieces'][0] = 54
    assert ludo_ai._destination(gs, 0, 54, 3) == 55
    result = l.move_piece(gs, 0)
    assert result['path'] == [55] and result['finished']
    assert gs['players'][0]['finished_count'] == 1


@pytest.mark.parametrize('teams', ['off', '2v2'])
@pytest.mark.parametrize('switches', [True, False])
def test_full_variant_ai_games_are_legal_and_terminate(teams, switches):
    gs = l.create_game(4, teams=teams, capture=switches,
                       color_jump=switches, exact_finish=switches)
    rng = random.Random(26)
    for _ in range(20000):
        if gs['finished']:
            break
        assert l.roll_dice(gs, rng.randint(1, 6))['ok']
        if gs['phase'] == 'move' and not gs['finished']:
            move = ludo_ai.best_move(gs, 'hard', rng)
            assert move in gs['movable_pieces']
            assert l.move_piece(gs, move)['ok']
    assert gs['finished']
