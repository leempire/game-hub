"""Pure, server-authoritative Turtle Soup submissions, judgments and projections."""

from copy import deepcopy
import re
import unicodedata

MAX_PLAYERS = 8
MAX_REQUESTS = 80
ANSWERS = {'yes': '是', 'no': '否', 'uncertain': '不确定'}
_REQUEST_ID = re.compile(r'^[A-Za-z0-9_-]{8,64}$')


def create_game(puzzle, player_ids, match_id, owner_id):
    if not 1 <= len(player_ids) <= MAX_PLAYERS or len(set(player_ids)) != len(player_ids):
        raise ValueError('海龟汤需要 1–8 名玩家')
    return {
        'match_id': match_id, 'revision': 0, 'phase': 'playing', 'ai_owner_id': owner_id,
        'puzzle_id': puzzle['id'], 'title': puzzle['title'], 'surface': puzzle['surface'],
        'solution': puzzle['solution'], 'essential_facts': deepcopy(puzzle['essential_facts']),
        'hints': deepcopy(puzzle.get('hints', [])),
        'hint_state': {'used': 0, 'last_at': 0, 'stalled': 0},
        'source': deepcopy(puzzle['source']), 'player_ids': list(player_ids),
        'active': list(player_ids), 'forfeited': [], 'requests': [], 'pending_id': None,
        'solved_by': None, 'winner': None, 'reason': None,
    }


def _text_valid(text, kind):
    limit = 200 if kind == 'ask' else 500
    return (isinstance(text, str) and 0 < len(text.strip()) <= limit and not any(
        unicodedata.category(char) in {'Cc', 'Cf', 'Cs', 'Zl', 'Zp'} for char in text))


def submit(gs, uid, data):
    request_id, kind, text = data.get('request_id'), data.get('kind'), data.get('text')
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        return '请求标识无效，请重新提交', False
    if data.get('match_id') != gs['match_id']:
        return '这次操作不属于当前对局', False
    previous = next((r for r in gs['requests'] if r['id'] == request_id and r['user_id'] == uid), None)
    if previous:
        return (None if previous['kind'] == kind and previous['text'] == text else '请求标识已使用'), False
    if gs['phase'] != 'playing' or uid not in gs['active']:
        return '你不在本局游戏中', False
    if type(data.get('revision')) is not int or data['revision'] != gs['revision']:
        return '对局已更新，请查看最新问答后重试', False
    if kind not in ('ask', 'guess') or not _text_valid(text, kind):
        return '提问限 1–200 字，猜汤底限 1–500 字，请输入一行文字', False
    if gs['pending_id'] is not None:
        return 'AI 主持正在回答，请稍后提交', False
    gs['requests'].append({'id': request_id, 'user_id': uid, 'kind': kind, 'text': text,
                           'status': 'pending', 'answer': None, 'correct': None, 'error': None})
    gs['pending_id'] = [uid, request_id]
    gs['revision'] += 1
    return None, True


def pending(gs):
    return next((r for r in gs['requests'] if [r['user_id'], r['id']] == gs['pending_id']), None)


def _judgment(response, facts):
    if not isinstance(response, dict) or response.get('answer') not in ANSWERS:
        raise ValueError('主持回答格式无效')
    covered = response.get('covered_facts')
    if (type(response.get('solved')) is not bool or type(response.get('contradiction')) is not bool
            or not isinstance(covered, list) or any(type(i) is not int or i < 0 or i >= len(facts) for i in covered)):
        raise ValueError('主持判定格式无效')
    return (response['solved'] and not response['contradiction']
            and set(covered) == set(range(len(facts))))


def apply_response(gs, response):
    request = pending(gs)
    if gs['phase'] != 'playing' or request is None:
        return False
    correct = _judgment(response, gs['essential_facts']) and request['kind'] == 'guess'
    request.update(status='answered', answer=ANSWERS[response['answer']],
                   correct=correct if request['kind'] == 'guess' else None)
    gs['pending_id'] = None
    gs['revision'] += 1
    if correct:
        gs['solved_by'] = request['user_id']
        finish(gs, 'solved')
    elif len(gs['requests']) >= MAX_REQUESTS:
        finish(gs, 'limit')
    else:
        _maybe_hint(gs, request, response.get('progress'))
    return True


def _maybe_hint(gs, request, progress):
    """Only publish curated hints, at most three, with a three-answer cooldown."""
    state = gs.setdefault('hint_state', {'used': 0, 'last_at': 0, 'stalled': 0})
    count = sum(item['status'] == 'answered' for item in gs['requests'])
    state['stalled'] = 0 if progress == 'on_track' else state['stalled'] + 1
    hints = gs.get('hints', [])[:3]
    if (state['used'] >= len(hints) or count - state['last_at'] < 3
            or not (progress in ('off_track', 'unclear') or state['stalled'] >= 6)):
        return
    request['hint'] = hints[state['used']]
    state.update(used=state['used'] + 1, last_at=count, stalled=0)


def fail_pending(gs, message):
    request = pending(gs)
    if gs['phase'] != 'playing' or request is None:
        return False
    request.update(status='failed', error=message)
    gs['pending_id'] = None
    gs['revision'] += 1
    if len(gs['requests']) >= MAX_REQUESTS:
        finish(gs, 'limit')
    return True


def finish(gs, reason):
    if gs['phase'] == 'finished':
        return
    gs.update(phase='finished', reason=reason, winner='players' if reason == 'solved' else 'none', pending_id=None)
    gs['revision'] += 1


def depart(gs, uid):
    if uid not in gs['active'] or gs['phase'] != 'playing':
        return
    request = pending(gs)
    if request and request['user_id'] == uid:
        fail_pending(gs, '提问者已离开，本次请求不再应用')
    gs['active'].remove(uid)
    gs['forfeited'].append(uid)
    gs['revision'] += 1
    if uid == gs['ai_owner_id'] or not gs['active']:
        finish(gs, 'payer_left')


def view_for(gs, uid):
    return {
        'game': 'turtle_soup', 'match_id': gs['match_id'], 'revision': gs['revision'],
        'phase': gs['phase'], 'title': gs['title'], 'surface': gs['surface'],
        'player_ids': list(gs['player_ids']), 'active': list(gs['active']),
        'requests': deepcopy(gs['requests']), 'busy': gs['pending_id'] is not None,
        'request_limit': MAX_REQUESTS, 'question_max_length': 200, 'guess_max_length': 500,
        'can_submit': gs['phase'] == 'playing' and uid in gs['active'] and gs['pending_id'] is None,
        'can_reveal': gs['phase'] == 'playing' and uid == gs['ai_owner_id'],
    }


def snapshot(gs):
    if gs.get('phase') != 'finished':
        return None
    return {'game': 'turtle_soup', 'title': gs['title'], 'surface': gs['surface'],
            'solution': gs['solution'], 'source': deepcopy(gs['source']),
            'winner': gs['winner'], 'reason': gs['reason'], 'solved_by': gs['solved_by'],
            'request_count': len(gs['requests']), 'player_ids': list(gs['player_ids']),
            'forfeited': list(gs['forfeited'])}
