"""Idempotent schema for friendship, blocking, and durable direct messages."""


def create_schema(conn):
    statements = """
        CREATE TABLE IF NOT EXISTS friend_links (
            user_low INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            user_high INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            requester_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK(status IN ('pending','accepted','declined','cancelled','removed')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_low,user_high),
            CHECK(user_low < user_high),
            CHECK(requester_id = user_low OR requester_id = user_high)
        );
        CREATE INDEX IF NOT EXISTS friend_links_requests ON friend_links(requester_id,created_at);
        CREATE INDEX IF NOT EXISTS friend_links_high ON friend_links(user_high,status);
        CREATE TABLE IF NOT EXISTS social_blocks (
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            peer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY(owner_id,peer_id), CHECK(owner_id != peer_id)
        );
        CREATE TABLE IF NOT EXISTS direct_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            user_low INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            user_high INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            client_id TEXT NOT NULL,
            body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 1000),
            created_at TEXT NOT NULL,
            UNIQUE(sender_id,client_id), CHECK(user_low < user_high),
            CHECK((sender_id=user_low AND recipient_id=user_high) OR
                  (sender_id=user_high AND recipient_id=user_low))
        );
        CREATE INDEX IF NOT EXISTS direct_messages_pair ON direct_messages(user_low,user_high,id);
        CREATE INDEX IF NOT EXISTS direct_messages_inbox ON direct_messages(recipient_id,sender_id,id);
        CREATE INDEX IF NOT EXISTS direct_messages_rate ON direct_messages(sender_id,created_at);
        CREATE TABLE IF NOT EXISTS direct_reads (
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            peer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            through_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(owner_id,peer_id), CHECK(owner_id != peer_id)
        );
    """
    for statement in statements.split(';'):
        if statement.strip():
            conn.execute(statement)
