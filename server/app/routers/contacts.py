"""Authenticated account social API, independent from gameplay and spectator traffic."""
from typing import Literal
import unicodedata
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator
from app import friends, direct_messages, room_invites
from app.security import get_current_user_id

router = APIRouter(prefix='/api/social', tags=['social'])
MAX_ID = 9223372036854775807


class SocialBody(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='before')
    @classmethod
    def valid_unicode(cls, value):
        # JSON permits escaped unpaired surrogates. Reject before validation
        # errors can echo them into an otherwise unencodable JSON response.
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str) and any(unicodedata.category(c) == 'Cs' for c in item):
                raise HTTPException(400, '文字包含无效的Unicode字符')
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        return value


class RequestBody(SocialBody):
    user_id: int = Field(ge=1, le=MAX_ID, strict=True)


class MessageBody(SocialBody):
    body: StrictStr = Field(min_length=1, max_length=1000)
    client_id: StrictStr = Field(min_length=8, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')


class ReadBody(SocialBody):
    through_id: int = Field(ge=0, le=MAX_ID, strict=True)


class InviteBody(SocialBody):
    user_id: int = Field(ge=1, le=MAX_ID, strict=True)
    room_code: StrictStr = Field(min_length=6, max_length=6, pattern=r'^[A-Za-z0-9]{6}$')


@router.get('')
def inbox(actor: int = Depends(get_current_user_id)):
    # 邀请与好友/消息同一份快照下发，社交页一次轮询就能看到。
    return {**friends.snapshot(actor), 'invites': room_invites.pending(actor)['items']}


@router.get('/summary')
def summary(actor: int = Depends(get_current_user_id)):
    return {**friends.summary(actor), 'invite_total': room_invites.count(actor)}


@router.get('/invites')
def invites(actor: int = Depends(get_current_user_id)):
    return room_invites.pending(actor)


@router.post('/invites')
def invite(body: InviteBody, actor: int = Depends(get_current_user_id)):
    return room_invites.invite(actor, body.user_id, body.room_code)


@router.post('/invites/{invite_id}/decline')
def decline_invite(invite_id: int = Path(ge=1, le=MAX_ID), actor: int = Depends(get_current_user_id)):
    return room_invites.decline(actor, invite_id)


@router.get('/users')
def users(q: str = Query(min_length=1, max_length=20), actor: int = Depends(get_current_user_id)):
    return friends.search(actor, q)


@router.post('/requests')
def request(body: RequestBody, actor: int = Depends(get_current_user_id)):
    return friends.request(actor, body.user_id)


@router.post('/requests/{peer}/{action}')
def respond(action: Literal['accept', 'decline', 'cancel'], peer: int = Path(ge=1, le=MAX_ID),
            actor: int = Depends(get_current_user_id)):
    return friends.respond(actor, peer, action)


@router.delete('/friends/{peer}')
def remove(peer: int = Path(ge=1, le=MAX_ID), actor: int = Depends(get_current_user_id)):
    return friends.remove(actor, peer)


@router.post('/blocks/{peer}')
def block(peer: int = Path(ge=1, le=MAX_ID), actor: int = Depends(get_current_user_id)):
    return friends.block(actor, peer)


@router.delete('/blocks/{peer}')
def unblock(peer: int = Path(ge=1, le=MAX_ID), actor: int = Depends(get_current_user_id)):
    return friends.unblock(actor, peer)


@router.get('/chats/{peer}')
def history(peer: int = Path(ge=1, le=MAX_ID), before: int | None = Query(default=None, ge=1, le=MAX_ID),
            after: int | None = Query(default=None, ge=0, le=MAX_ID), limit: int = Query(default=50, ge=1, le=100),
            actor: int = Depends(get_current_user_id)):
    return direct_messages.history(actor, peer, before, after, limit)


@router.post('/chats/{peer}')
def send(body: MessageBody, peer: int = Path(ge=1, le=MAX_ID), actor: int = Depends(get_current_user_id)):
    return direct_messages.send(actor, peer, body.body, body.client_id)


@router.post('/chats/{peer}/read')
def read(body: ReadBody, peer: int = Path(ge=1, le=MAX_ID), actor: int = Depends(get_current_user_id)):
    return direct_messages.read(actor, peer, body.through_id)
