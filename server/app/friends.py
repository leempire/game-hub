"""Friend requests, explicit consent, removal, blocking, and social inbox summaries."""
from fastapi import HTTPException
from app.social_common import (active_peer, blocked, check_capacity, connection, link,
                               pair, profile, timestamp)


def search(actor, username):
    with connection() as conn:
        user = conn.execute('SELECT id,username,nickname FROM users WHERE username=? AND banned=0',
                            (username.strip(),)).fetchone()
        if user is None or user['id'] == actor or blocked(conn, actor, user['id']):
            return {'items': []}
        relation = link(conn, actor, user['id'])
        state = 'none'
        if relation and relation['status'] == 'accepted':
            state = 'friend'
        elif relation and relation['status'] == 'pending':
            state = 'outgoing' if relation['requester_id'] == actor else 'incoming'
        return {'items': [{**profile(user), 'relation': state}]}


def request(actor, peer):
    with connection(True) as conn:
        active_peer(conn, actor, peer)
        current = link(conn, actor, peer)
        if current and current['status'] == 'accepted':
            return {'status': 'accepted'}
        if current and current['status'] == 'pending':
            if current['requester_id'] != actor:
                raise HTTPException(409, '对方已向你申请，请在好友申请中处理')
            return {'status': 'pending'}
        if current and current['updated_at'] > timestamp(60):
            raise HTTPException(429, '请稍后再申请，至少间隔一分钟')
        check_capacity(conn, actor, peer)
        recent = conn.execute('SELECT COUNT(*) FROM friend_links WHERE requester_id=? AND created_at>=?',
                              (actor, timestamp(60))).fetchone()[0]
        if recent >= 20:
            raise HTTPException(429, '申请太频繁，请一分钟后重试')
        now = timestamp()
        conn.execute('''INSERT INTO friend_links VALUES(?,?,?,'pending',?,?)
            ON CONFLICT(user_low,user_high) DO UPDATE SET requester_id=excluded.requester_id,
            status='pending',created_at=excluded.created_at,updated_at=excluded.updated_at''',
                     (*pair(actor, peer), actor, now, now))
    return {'status': 'pending'}


def respond(actor, peer, action):
    with connection(True) as conn:
        active_peer(conn, actor, peer)
        current = link(conn, actor, peer)
        target = {'accept': 'accepted', 'decline': 'declined', 'cancel': 'cancelled'}[action]
        authorized = current and ((current['requester_id'] == actor) == (action == 'cancel'))
        if not authorized or current['status'] not in ('pending', target):
            raise HTTPException(404, '申请不存在或已经处理')
        if current['status'] != target:
            if action == 'accept':
                check_capacity(conn, actor, peer)
            conn.execute('UPDATE friend_links SET status=?,updated_at=? WHERE user_low=? AND user_high=?',
                         (target, timestamp(), *pair(actor, peer)))
    return {'ok': True}


def remove(actor, peer):
    with connection(True) as conn:
        row = link(conn, actor, peer)
        if row is None or row['status'] != 'accepted':
            raise HTTPException(404, '好友不存在')
        conn.execute("UPDATE friend_links SET status='removed',updated_at=? WHERE user_low=? AND user_high=?",
                     (timestamp(), *pair(actor, peer)))
    return {'ok': True}


def block(actor, peer):
    with connection(True) as conn:
        pair(actor, peer)
        if conn.execute('SELECT 1 FROM users WHERE id=?', (peer,)).fetchone() is None:
            raise HTTPException(404, '用户不存在')
        conn.execute('INSERT OR IGNORE INTO social_blocks VALUES(?,?,?)', (actor, peer, timestamp()))
        conn.execute("UPDATE friend_links SET status='removed',updated_at=? WHERE user_low=? AND user_high=?",
                     (timestamp(), *pair(actor, peer)))
    return {'ok': True}


def unblock(actor, peer):
    with connection(True) as conn:
        conn.execute('DELETE FROM social_blocks WHERE owner_id=? AND peer_id=?', (actor, peer))
    return {'ok': True}


def snapshot(actor):
    with connection() as conn:
        rows = conn.execute('''SELECT u.id,u.username,u.nickname,f.status,f.requester_id,f.created_at,
            m.id AS message_id,m.sender_id,m.body,m.created_at AS message_at,
            (SELECT COUNT(*) FROM direct_messages d WHERE d.recipient_id=? AND d.sender_id=u.id
              AND d.id>COALESCE(r.through_id,0)) AS unread_count
            FROM friend_links f JOIN users u ON u.id=CASE WHEN f.user_low=? THEN f.user_high ELSE f.user_low END
            LEFT JOIN direct_reads r ON r.owner_id=? AND r.peer_id=u.id
            LEFT JOIN direct_messages m ON m.id=(SELECT id FROM direct_messages d
              WHERE d.user_low=f.user_low AND d.user_high=f.user_high ORDER BY id DESC LIMIT 1)
            WHERE (f.user_low=? OR f.user_high=?) AND f.status IN ('pending','accepted') AND u.banned=0
              AND NOT EXISTS(SELECT 1 FROM social_blocks b WHERE
                (b.owner_id=? AND b.peer_id=u.id) OR (b.owner_id=u.id AND b.peer_id=?))
            ORDER BY COALESCE(m.id,0) DESC,f.updated_at DESC,u.id''', (actor,) * 7).fetchall()
        result = {'friends': [], 'incoming': [], 'outgoing': [], 'blocked': [], 'unread_total': 0}
        for row in rows:
            item = profile(row)
            if row['status'] == 'accepted':
                item['unread_count'] = row['unread_count']
                item['last_message'] = ({'id': row['message_id'], 'sender_id': row['sender_id'],
                                         'body': row['body'], 'created_at': row['message_at']}
                                        if row['message_id'] is not None else None)
                result['friends'].append(item)
                result['unread_total'] += row['unread_count']
            else:
                item['created_at'] = row['created_at']
                result['outgoing' if row['requester_id'] == actor else 'incoming'].append(item)
        result['blocked'] = [profile(row) for row in conn.execute('''SELECT u.id,u.username,u.nickname
            FROM social_blocks b JOIN users u ON u.id=b.peer_id WHERE b.owner_id=? ORDER BY b.created_at DESC''', (actor,))]
        return result


def summary(actor):
    """Indexed counts for the navigation badge, without conversation bodies."""
    with connection() as conn:
        unread = conn.execute('''SELECT COUNT(*) FROM direct_messages d
            JOIN friend_links f ON f.user_low=d.user_low AND f.user_high=d.user_high AND f.status='accepted'
            JOIN users u ON u.id=d.sender_id AND u.banned=0
            LEFT JOIN direct_reads r ON r.owner_id=? AND r.peer_id=u.id
            WHERE d.recipient_id=? AND d.id>COALESCE(r.through_id,0)
              AND NOT EXISTS(SELECT 1 FROM social_blocks b WHERE
                (b.owner_id=? AND b.peer_id=u.id) OR (b.owner_id=u.id AND b.peer_id=?))''', (actor,) * 4).fetchone()[0]
        incoming = conn.execute('''SELECT COUNT(*) FROM friend_links f
            JOIN users u ON u.id=f.requester_id AND u.banned=0
            WHERE f.status='pending' AND f.requester_id!=? AND (f.user_low=? OR f.user_high=?)
              AND NOT EXISTS(SELECT 1 FROM social_blocks b WHERE
                (b.owner_id=? AND b.peer_id=u.id) OR (b.owner_id=u.id AND b.peer_id=?))''', (actor,) * 5).fetchone()[0]
        return {'unread_total': unread, 'incoming_total': incoming}
