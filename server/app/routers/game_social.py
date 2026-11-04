"""Private practice, whitelisted phrases and authenticated read-only watching."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import BaseModel, ConfigDict, Field

from app import practice, quick_chat, spectating
from app.database import get_conn, row_to_user
from app.security import get_current_user_id
from app.ws import _broadcast

router = APIRouter(tags=['game-social'])


class QuickPhraseRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=24, strict=True)


class WatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str | None = Field(default=None, max_length=32)


def _user(user_id):
    conn = get_conn()
    try:
        row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '用户不存在')
        return row_to_user(row)
    finally:
        conn.close()


@router.post('/api/practice/{game}')
async def start_practice(game: str, user_id: int = Depends(get_current_user_id)):
    user = await asyncio.to_thread(_user, user_id)
    try:
        return practice.start_practice(user, game)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/api/rooms/{code}/quick-chat')
async def send_quick_phrase(code: str, request: QuickPhraseRequest,
                            user_id: int = Depends(get_current_user_id)):
    try:
        message = quick_chat.make_message(code, user_id, request.id)
    except quick_chat.ChatError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    await _broadcast(code, message)
    await spectating.broadcast_public(code, message)
    return {'ok': True, 'message': message, 'cooldown_seconds': quick_chat.COOLDOWN_SECONDS}


@router.post('/api/rooms/{code}/watch')
async def authorize_watch(code: str, request: WatchRequest,
                          user_id: int = Depends(get_current_user_id)):
    try:
        return spectating.grant_access(code, user_id, request.password)
    except spectating.WatchError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.websocket('/ws/watch/{code}')
async def watch(socket: WebSocket, code: str):
    await spectating.watch_socket(socket, code)
