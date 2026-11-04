"""Authorized text conversations, retry idempotency, stable cursors and read receipts."""
import unicodedata
from fastapi import HTTPException
from app.social_common import connection, pair, profile, require_friend, timestamp


def message(row, actor):
    return {**{key: row[key] for key in ('id', 'sender_id', 'body', 'created_at')},
            'client_id': row['client_id'] if row['sender_id'] == actor else None}


def send(actor, peer, body, client_id):
    body = body.strip()
    if not body or any(unicodedata.category(c) in ('Cc', 'Cs') and c not in '\n\t' for c in body):
        raise HTTPException(400, '请输入有效文字，不能包含控制字符')
    with connection(True) as conn:
        require_friend(conn, actor, peer)
        old = conn.execute('SELECT * FROM direct_messages WHERE sender_id=? AND client_id=?',
                           (actor, client_id)).fetchone()
        if old:
            if old['recipient_id'] != peer or old['body'] != body:
                raise HTTPException(409, '重复发送标识对应的内容不一致')
            return message(old, actor)
        count = conn.execute('SELECT COUNT(*) FROM direct_messages WHERE sender_id=? AND created_at>=?',
                             (actor, timestamp(10))).fetchone()[0]
        if count >= 10:
            raise HTTPException(429, '发送太快，请稍后重试')
        cursor = conn.execute('''INSERT INTO direct_messages
            (sender_id,recipient_id,user_low,user_high,client_id,body,created_at) VALUES(?,?,?,?,?,?,?)''',
                              (actor, peer, *pair(actor, peer), client_id, body, timestamp()))
        row = conn.execute('SELECT * FROM direct_messages WHERE id=?', (cursor.lastrowid,)).fetchone()
        return message(row, actor)


def history(actor, peer, before=None, after=None, limit=50):
    if before is not None and after is not None:
        raise HTTPException(400, '不能同时指定前后游标')
    with connection() as conn:
        user = require_friend(conn, actor, peer)
        direction = 'ASC' if after is not None else 'DESC'
        predicate = ' AND id>?' if after is not None else ' AND id<?' if before is not None else ''
        params = [*pair(actor, peer)]
        if after is not None or before is not None:
            params.append(after if after is not None else before)
        rows = conn.execute('SELECT * FROM direct_messages WHERE user_low=? AND user_high=?'+predicate+
                            f' ORDER BY id {direction} LIMIT ?', (*params, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        if after is None:
            rows = list(reversed(rows))
        read = conn.execute('SELECT through_id FROM direct_reads WHERE owner_id=? AND peer_id=?',
                            (peer, actor)).fetchone()
        return {'peer': profile(user), 'items': [message(row, actor) for row in rows],
                'has_more': more, 'peer_read_through': read[0] if read else 0}


def read(actor, peer, through):
    with connection(True) as conn:
        require_friend(conn, actor, peer)
        actual = conn.execute('''SELECT COALESCE(MAX(id),0) FROM direct_messages
            WHERE recipient_id=? AND sender_id=? AND id<=?''', (actor, peer, through)).fetchone()[0]
        conn.execute('''INSERT INTO direct_reads VALUES(?,?,?) ON CONFLICT(owner_id,peer_id)
            DO UPDATE SET through_id=MAX(direct_reads.through_id,excluded.through_id)''', (actor, peer, actual))
        value = conn.execute('SELECT through_id FROM direct_reads WHERE owner_id=? AND peer_id=?',
                             (actor, peer)).fetchone()[0]
        return {'through_id': value}
