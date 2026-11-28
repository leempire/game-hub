"""Shared paid AI gateway. All games use this entry for JSON model calls.

Owner locks span games and rooms. One logical operation gets at most one request;
usage is settled even if the answer is invalid or the game has moved on.
"""

import asyncio
import json
import os
from weakref import WeakValueDictionary

import httpx

from app import ai_credits
from app.ai_pricing import MIN_START_CREDITS

BASE_URL = 'https://ark.cn-beijing.volces.com/api/plan/v3'
MODEL = 'deepseek-v4.1-flash-economy'
REASONING_EFFORT = 'low'
PAID_BOT_GAMES = frozenset({'undercover'})
_HOST_LOCKS = WeakValueDictionary()


def uses_paid_ai(room):
    return room['game'] in PAID_BOT_GAMES and any(p.get('is_bot') for p in room['players'])


def require_game_start(room):
    if not uses_paid_ai(room):
        return
    if not configured():
        raise ValueError('AI 服务未配置 ARK_AGENT_API_KEY')
    if ai_credits.balance_units(room['host_id']) < MIN_START_CREDITS * ai_credits.UNITS:
        raise ValueError(f'有 AI 人机的对局需要房主 AI 积分至少为 {MIN_START_CREDITS}')


def configured():
    return bool(os.environ.get('ARK_AGENT_API_KEY'))


def _host_lock(user_id):
    key = (asyncio.get_running_loop(), user_id)
    lock = _HOST_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _HOST_LOCKS[key] = lock
    return lock


async def call_json(messages, user_id, operation_id, is_current=lambda: True):
    """Never retry a potentially billable request. Charge even if the phase expired."""
    async with _host_lock(user_id):
        if not is_current():
            return None
        key = os.environ.get('ARK_AGENT_API_KEY')
        if not key:
            raise ValueError('AI 服务未配置 ARK_AGENT_API_KEY')
        if not ai_credits.begin_call(operation_id, user_id, MODEL):
            return None
        payload = {
            'model': MODEL, 'reasoning_effort': REASONING_EFFORT, 'max_tokens': 1024,
            'messages': messages,
            'response_format': {'type': 'json_object'}, 'stream': False,
        }
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.post(BASE_URL + '/chat/completions', json=payload,
                                             headers={'Authorization': 'Bearer ' + key})
                response.raise_for_status()
                result = response.json()
            # Settle before parsing: malformed and stale answers still used tokens.
            ai_credits.settle_call(operation_id, result['usage'])
        except (httpx.HTTPError, ValueError, KeyError, TypeError, asyncio.CancelledError):
            ai_credits.mark_unknown(operation_id)
            raise
        action = json.loads(result['choices'][0]['message']['content'])
        if not isinstance(action, dict):
            raise ValueError('AI 返回的操作格式无效')
        return action
