"""Event-window UNO arbitration. Private +4 evidence never enters a sync payload."""

from app.game import uno as engine


def wild4_allowed(game, seat, card):
    return not (card == 'W+4' and game['rules'].get('wild4_strict', False)
                and any(engine.card_color(c) == game['current_color']
                        for c in game['hands'].get(seat, [])))


def begin_play(game, seat, card):
    """Called only after validation, before modifying the hand or effective color."""
    evidence = None
    if (card == 'W+4' and game['rules'].get('wild4_challenge', False)
            and game['pending_draw'] == 0):
        evidence = {'offender': seat,
                    'guilty': any(engine.card_color(c) == game['current_color']
                                  for c in game['hands'].get(seat, []))}
    before = {s: (tuple(hand), bool(game["uno_called"].get(s)))
              for s, hand in game["hands"].items()}
    close_windows(game)
    return {"challenge": evidence, "hands": before}


def close_windows(game):
    game.pop('wild4_pending', None)
    game.pop('uno_vulnerable', None)


def finish_play(game, context):
    evidence = context["challenge"]
    if evidence is not None and not game['finished'] and game['pending_draw'] == 4:
        game['wild4_pending'] = {**evidence, 'target': game['turn']}
    if game['rules'].get('uno_penalty', False) and not game['finished']:
        game['uno_vulnerable'] = [seat for seat in engine.active_seats(game)
                                  if len(game['hands'].get(seat, [])) == 1
                                  and not game['uno_called'].get(seat)
                                  and context["hands"].get(seat, ((), False))
                                  != (tuple(game["hands"].get(seat, [])), False)]


def challenge_targets(game, seat):
    pending = game.get('wild4_pending')
    if (pending and not game['finished'] and game['turn'] == seat
            and pending['target'] == seat and seat not in game['out']):
        return [pending['offender']]
    return []


def challenge_draw4(game, seat):
    """Successful challenge reverses four; failed challenge draws six and skips."""
    if not challenge_targets(game, seat):
        return {'ok': False, 'error': '现在没有可以质疑的变色+4'}
    evidence = game['wild4_pending']
    guilty = evidence['guilty']
    victim = evidence['offender'] if guilty else seat
    count = 4 if guilty else 6
    close_windows(game)
    game['pending_draw'], game['pending_kind'] = 0, None
    game['drawn_this_turn'] = None
    cards = engine._take(game, count)
    engine._give(game, victim, cards)
    if not guilty or seat in game['out']:
        engine._advance(game, 1)
    engine._settle_turn(game)
    return {'ok': True, 'success': guilty, 'seat': seat, 'offender': evidence['offender'],
            'penalty_seat': victim, 'penalty': len(cards),
            'game_over': engine._game_over_payload(game)}


def catch_targets(game, seat):
    if game['finished'] or seat is None or seat in game['out']:
        return []
    return [target for target in game.get('uno_vulnerable', [])
            if target != seat and target not in game['out']
            and (engine.team_of(game, seat) is None
                 or engine.team_of(game, target) != engine.team_of(game, seat))
            and len(game['hands'].get(target, [])) == 1
            and not game['uno_called'].get(target)]


def catch_uno(game, seat, target):
    """Catch before the next accepted play/draw; calling UNO closes that exposure."""
    if isinstance(target, bool) or not isinstance(target, int) or target not in catch_targets(game, seat):
        return {'ok': False, 'error': '他现在不需要因漏喊UNO受罚'}
    cards = engine._take(game, 2)
    engine._give(game, target, cards)
    game['uno_vulnerable'] = [s for s in game.get('uno_vulnerable', []) if s != target]
    return {'ok': True, 'seat': seat, 'target': target, 'penalty': len(cards),
            'game_over': engine._game_over_payload(game)}


def finish_last_penalty(game, seat, card):
    """Official scoring includes a final +2/+4, whose now-empty hand cannot bluff."""
    if (game['rules'].get('score_target') != 500 and not game['rules'].get('wild4_challenge')):
        return
    if card not in ('W+4', 'R+2', 'G+2', 'B+2', 'Y+2'):
        return
    if game["rules"]["teams"] == "2v2":
        mate = engine._mate_seat(game, seat)
        if mate is not None and game["hands"].get(mate):
            return
    order = engine.active_seats(game)
    if len(order) < 2:
        return
    target = order[(order.index(seat) + game['direction']) % len(order)]
    count = 4 if card == 'W+4' else 2
    engine._give(game, target, engine._take(game, count + game['pending_draw']))
    game['pending_draw'], game['pending_kind'] = 0, None
