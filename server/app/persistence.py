"""房间状态实时落盘与启动恢复（热重启支持）。

设计：
- rooms 表存整房 JSON（code 主键），room_manager 每次状态变更写盘（写穿）
- 自定义编码器处理三类非 JSON 结构：
  * int 键 dict（玩家 id / 扫雷&飞行棋座位号）→ {"__kv__": [[key, val], ...]}
  * set → {"__set__": [...]}（rematch 已删除；解码保留以兼容旧落盘数据）
  * random.Random（2048 出块 rng）→ {"__rng__": [version, internal, gauss_next]}
- 未 init() 时全部操作 no-op（单元测试直接 import room_manager 不触库）
- 单房数据损坏只跳过该房，不影响整体恢复
"""

import json
import random
import time

from app import database

_enabled = False


def init():
    """建 rooms 表并启用落盘。main.py startup 在 init_db() 之后调用。"""
    global _enabled
    conn = database.get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                code TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()
    _enabled = True


def disable():
    """关闭落盘（测试隔离用）。"""
    global _enabled
    _enabled = False


def save_room(room: dict):
    """整房写盘（INSERT OR REPLACE）。未启用时 no-op。"""
    if not _enabled:
        return
    data = dumps_state(room)
    conn = database.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO rooms (code, data, updated_at) VALUES (?, ?, ?)",
            (room["code"], data, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def delete_room(code: str):
    """房间销毁时删行。未启用时 no-op。"""
    if not _enabled:
        return
    conn = database.get_conn()
    try:
        conn.execute("DELETE FROM rooms WHERE code = ?", (code,))
        conn.commit()
    finally:
        conn.close()


def load_all_rooms() -> list[dict]:
    """读回全部房间（已解码）。未启用时返回空表。"""
    if not _enabled:
        return []
    conn = database.get_conn()
    try:
        rows = conn.execute("SELECT code, data FROM rooms").fetchall()
    finally:
        conn.close()
    rooms = []
    for r in rows:
        try:
            rooms.append(loads_state(r["data"]))
        except Exception as exc:
            # 单房损坏只跳过该房——但**必须出声**：静默跳过会让「房间恢复不了」
            # 变成一个查不出来的幽灵（落盘行还在、内存里没有、sweeper 也碰不到）。
            print(f"[persistence] 跳过损坏房间 {r['code']}: {type(exc).__name__}: {exc}",
                  flush=True)
            continue
    return rooms


# ── 编解码 ────────────────────────────────────────────────────


def _encode(obj):
    if isinstance(obj, random.Random):
        version, internal, gauss_next = obj.getstate()
        return {"__rng__": [version, list(internal), gauss_next]}
    if isinstance(obj, (set, frozenset)):
        return {"__set__": [_encode(x) for x in obj]}
    if isinstance(obj, dict):
        if all(isinstance(k, str) for k in obj):
            return {k: _encode(v) for k, v in obj.items()}
        # 含非 str 键（int 玩家 id / 座位号）→ 键值对列表
        return {"__kv__": [[_encode_key(k), _encode(v)] for k, v in obj.items()]}
    if isinstance(obj, (list, tuple)):
        return [_encode(x) for x in obj]
    return obj


def _encode_key(key):
    if isinstance(key, int):
        return {"__int__": key}
    if isinstance(key, tuple):
        # **tuple 键必须打标记**：JSON 没有 tuple，不打标记就退化成数组，
        # 解码时当字典键会 TypeError: unhashable type: 'list'。扫雷的
        # game_state["revealed"] 就是 dict[tuple[int,int], int]——漏了这条
        # 整个扫雷房都恢复不回来（而且是静默跳过，查都查不出来）。
        return {"__tuple__": [_encode(x) for x in key]}
    return key


def _decode_hook(d: dict):
    if "__int__" in d:
        return d["__int__"]
    if "__tuple__" in d:
        return tuple(_decode_hook(x) if isinstance(x, dict) else x for x in d["__tuple__"])
    if "__set__" in d:
        # 集合里的元素必然可哈希，所以 list 一定是原来的 tuple（JSON 没有 tuple
        # 类型）——扫雷的 flagged / mine_positions 就是 set[tuple[int, int]]。
        # 直接 set() 会 TypeError: unhashable type: 'list'，而 load_all_rooms
        # 对单房解码失败是**静默跳过**的：整个房间直接消失、永远恢复不回来，
        # 落盘行还赖在表里不走（踩过一次：5 个房间全恢复不了）。
        return {tuple(x) if isinstance(x, list) else x for x in d["__set__"]}
    if "__rng__" in d:
        version, internal, gauss_next = d["__rng__"]
        rng = random.Random()
        rng.setstate((version, tuple(internal), gauss_next))
        return rng
    if "__kv__" in d:
        # 键可能是 list：① 老数据（_encode_key 还没给 tuple 打标记时落盘的）
        # ② 漏打标记的路径。数组当字典键会 TypeError，这里兜住。
        return {
            (tuple(k) if isinstance(k, list) else k): v
            for k, v in d["__kv__"]
        }

    return d


def dumps_state(obj) -> str:
    """任意房间/game_state 结构 → JSON 字符串（保留 int 键/set/rng）。"""
    return json.dumps(_encode(obj), ensure_ascii=False)


def loads_state(data: str):
    """dumps_state 的逆操作。"""
    return json.loads(data, object_hook=_decode_hook)
