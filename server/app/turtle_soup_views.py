"""Whitelisted soup wire views; source, solution and host context stay private."""

from copy import deepcopy

from app import ai_credits
from app.game import turtle_soup


def playing_view(room, uid):
    view = turtle_soup.view_for(room['game_state'], uid)
    enabled = ai_credits.balance_units(room['game_state']['ai_owner_id']) >= 0
    view['ai_enabled'] = enabled
    view['can_submit'] = view['can_submit'] and enabled
    return view


def result_for(room, uid):
    gs = room.get('game_state') or {}
    summary = gs.get('summary')
    roster = room.get('_turtle_soup_roster', [])
    if not summary or not any(p['id'] == uid for p in roster):
        return None
    return {'type': 'game_over', 'game': 'turtle_soup', 'match_id': gs['match_id'],
            'winner': summary['winner'], 'reason': summary['reason'], 'summary': deepcopy(summary)}
