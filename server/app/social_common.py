"""Shared SQL transaction and authorization helpers for account social features."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from app import database

FRIEND_LIMIT = 200


def timestamp(seconds_ago=0):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(timespec='milliseconds')


@contextmanager
def connection(write=False):
    conn = database.get_conn()
    try:
        # Keep authorization and data on one consistent SQLite snapshot.
        conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield conn
        if write:
            conn.commit()
    finally:
        conn.close()


def pair(actor, peer):
    if actor == peer:
        raise HTTPException(400, '不能添加自己或给自己发消息')
    return min(actor, peer), max(actor, peer)


def profile(row):
    return {key: row[key] for key in ('id', 'username', 'nickname')}


def active_peer(conn, actor, peer):
    pair(actor, peer)
    row = conn.execute('SELECT id,username,nickname FROM users WHERE id=? AND banned=0', (peer,)).fetchone()
    if row is None:
        raise HTTPException(404, '用户不存在或暂不可用')
    if blocked(conn, actor, peer):
        raise HTTPException(403, '当前无法与该用户互动')
    return row


def blocked(conn, actor, peer):
    return conn.execute('''SELECT 1 FROM social_blocks WHERE
        (owner_id=? AND peer_id=?) OR (owner_id=? AND peer_id=?)''',
                        (actor, peer, peer, actor)).fetchone() is not None


def link(conn, actor, peer):
    return conn.execute('SELECT * FROM friend_links WHERE user_low=? AND user_high=?', pair(actor, peer)).fetchone()


def require_friend(conn, actor, peer):
    user = active_peer(conn, actor, peer)
    row = link(conn, actor, peer)
    if row is None or row['status'] != 'accepted':
        raise HTTPException(403, '成为好友后才能聊天')
    return user


def check_capacity(conn, *users):
    for user in users:
        count = conn.execute('''SELECT COUNT(*) FROM friend_links
            WHERE status='accepted' AND (user_low=? OR user_high=?)''', (user, user)).fetchone()[0]
        if count >= FRIEND_LIMIT:
            raise HTTPException(409, '好友数量已达上限（200）')
