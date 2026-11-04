"""Whitelisted public game phrases; no caller-provided free text is broadcast."""

import time

from app import room_manager as rm
from app.quick_phrases import PHRASES, phrases_for

COOLDOWN_SECONDS = 2.0
_last_sent: dict[tuple[str, int], float] = {}


class ChatError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def make_message(code: str, user_id: int, phrase_id, now: float | None = None) -> dict:
    room = rm.get_room(code)
    if not room:
        raise ChatError('房间不存在', 404)
    if room['game'] not in PHRASES:
        raise ChatError('这款游戏没有局内快捷语')
    player = next((p for p in room['players'] if p['id'] == user_id), None)
    if not player or player.get('is_bot') or not rm.user_in_game(room, user_id):
        raise ChatError('只有本局真人玩家可以发送快捷语', 403)
    choices = dict(PHRASES[room['game']])
    if not isinstance(phrase_id, str) or phrase_id not in choices:
        raise ChatError('请选择列表中的快捷语')
    current = time.monotonic() if now is None else now
    key = code, user_id
    if current - _last_sent.get(key, float('-inf')) < COOLDOWN_SECONDS:
        raise ChatError('发送太快，请稍等2秒', 429)
    _last_sent[key] = current
    # Prune expired windows without retaining room/player state indefinitely.
    if len(_last_sent) > 512:
        for old_key, sent in list(_last_sent.items()):
            if current - sent > 60 or rm.get_room(old_key[0]) is None:
                _last_sent.pop(old_key, None)
    return {'type': 'quick_chat', 'id': phrase_id, 'user_id': user_id,
            'name': player.get('nickname') or player['username'], 'text': choices[phrase_id]}
