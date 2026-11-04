"""Pure room-owned 500-point UNO series; round IDs prevent double settlement."""

from copy import deepcopy
from app.game.uno import hand_points, team_of


def create_series(user_ids, teams='off', target=500):
    if target != 500 or teams not in ('off', '2v2') or len(set(user_ids)) != len(user_ids):
        raise ValueError('无效的UNO计分赛配置')
    if len(user_ids) not in (2, 3, 4) or (teams == '2v2' and len(user_ids) != 4):
        raise ValueError('UNO计分赛需要2至4人，组队需要4人')
    return {'user_ids': list(user_ids), 'teams': teams, 'target': target,
            'scores': {str(uid): 0 for uid in user_ids}, 'round_ids': [],
            'finished': False, 'winners': []}


def add_round(series, game, round_id):
    """Return a new series, award the winner opponents' remaining hand points.

    Roster and team assignments must remain identical; room layer resets a series
    after membership/seat/rules changes. Only an ordinary completed hand scores;
    surrender/empty-room outcomes cannot be farmed for points.
    """
    if round_id in series['round_ids']:
        return deepcopy(series)
    if series['finished']:
        raise ValueError('本轮计分赛已结束，请重新开始')
    if list(game['seat_uids']) != series['user_ids'] or game['rules']['teams'] != series['teams']:
        raise ValueError('计分赛成员或座位变化，需要重新开始计分')
    if not game['finished']:
        raise ValueError('本局还未结束')
    result = deepcopy(series)
    result['round_ids'].append(round_id)
    winner = game.get('winner')
    if winner is None or game.get('reason') not in ('emptied', 'team_emptied'):
        return result
    winners = [seat for seat in range(game['num_players'])
               if (team_of(game, seat) == game.get('winner_team')
                   if series['teams'] == '2v2' else seat == winner)]
    points = sum(hand_points(hand) for seat, hand in game['hands'].items() if seat not in winners)
    for seat in winners:
        uid = str(game['seat_uids'][seat])
        result['scores'][uid] += points
    result['winners'] = [uid for uid in result['user_ids'] if result['scores'][str(uid)] >= result['target']]
    result['finished'] = bool(result['winners'])
    result['last_points'] = points
    return result
