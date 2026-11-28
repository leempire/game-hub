"""Idempotent AI ledger schema."""


def create_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS ai_usage (
        operation_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
        model TEXT NOT NULL, status TEXT NOT NULL, cached_tokens INTEGER,
        uncached_tokens INTEGER, output_tokens INTEGER, cost_units INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS ai_credit_adjustments (
        id INTEGER PRIMARY KEY, admin_id INTEGER NOT NULL REFERENCES users(id),
        user_id INTEGER NOT NULL REFERENCES users(id), previous_units INTEGER NOT NULL,
        new_units INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

