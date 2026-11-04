"""Private, unranked bot practice rooms for the four tutorial-heavy games."""

import secrets

from app import room_manager as rm

GAMES = ('stasis', 'luzhanqi', 'battleship', 'uno')


def start_practice(user: dict, game: str) -> dict:
    """Create/reuse a room; normal lobby start/return rules remain in force.

    Called on the event-loop thread without awaits: duplicate requests cannot
    create parallel rooms between lookup and insertion.
    """
    if game not in GAMES:
        raise ValueError('这款游戏没有新手人机练习')
    for room in list(rm._rooms.values()):
        if (room.get('practice') and room.get('practice_owner_id') == user['id']
                and room['game'] == game and any(p['id'] == user['id'] for p in room['players'])
                and (room['status'] == 'waiting' or rm.user_in_game(room, user['id']))):
            return {'code': room['code'], 'room': rm.room_to_dict(room), 'reused': True}
    # A random internal password also protects a practice room until older
    # clients/servers learn the explicit private-room admission guard.
    room = rm.create_room(user, '人机练习', secrets.token_urlsafe(24), game)
    room['practice'] = True
    room['practice_owner_id'] = user['id']
    room['allow_spectators'] = False
    rm.add_bot(room['code'], user['id'], 'normal')
    rm._touch_activity(room)
    return {'code': room['code'], 'room': rm.room_to_dict(room), 'reused': False}
