"""Persistent account-level soup history, independent of final game records."""


def create_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS turtle_soup_seen (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        puzzle_key TEXT NOT NULL, match_id TEXT NOT NULL,
        played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id,puzzle_key))''')
