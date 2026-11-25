"""深渊回声局外天赋的持久化表（幂等）。

与 `social_schema.py` / `competition_records.py` 同一模式：只建表，不碰业务。
`database._create_tables` 在建库时调用一次。
"""


def create_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS abyss_talents (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            points INTEGER NOT NULL DEFAULT 0,
            earned INTEGER NOT NULL DEFAULT 0,
            levels TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
    """)
    # 每局（房间 + 开局时间）对每个人只发一次。热重启、结算回调重入、
    # 玩家在结算后离开再次触发 settle，都靠这个主键去重。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS abyss_talent_awards (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            room_code TEXT NOT NULL,
            round_key TEXT NOT NULL,
            points INTEGER NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, room_code, round_key)
        )
    """)