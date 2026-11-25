"""深渊回声的局外天赋树：加点、免费重置、每局结算发点。

**为什么只做深渊回声**：它是 1–4 人合作生存，局外数值不会像棋类那样把对手
按在地上；其他房间制游戏（尤其是对抗类）保持「一局一清」，见
`docs/游戏设计/深空休眠/增强调研.md` 对局外成长的否决理由。

设计约束（完整论证见 `docs/游戏设计/深渊回声/设计.md`「局外天赋树」）：

- **上限很小**：全树点满约等于 2–3 个局内被动等级，改变开局手感但不替代 build；
- **趣味优先**：除了小数值，还有「开局武器 Lv.2 / 6 秒无敌 / 150 打捞分」这类
  一眼能感知、又不会滚雪球的开局节点；
- **免费重置**：所有节点随时全额返还，鼓励每局换一种起手，而不是逼玩家查攻略；
- **按表现发点、单局封顶 4 点**：完成 1 点打底，胜利 / 守卫 / 存活 / 火力 / 救援
  各 1 点；挂机或中途离开拿不到表现分。

本模块只读写 SQLite，不参与引擎推进；引擎通过 `bonus_for_user` 拿到纯字典，
保持 `server/app/game/` 不认识数据库的约定。
"""
import json
from datetime import datetime, timezone

from app import database

# 单局最多发放的天赋点。完成试潜本身 1 点，其余表现项各 1 点。
MAX_POINTS_PER_GAME = 4
# 「有参与」门槛：低于它只拿完成分，防止挂机被队友带赢也拿满。
ACTIVE_KILLS = 60
ACTIVE_DAMAGE = 300.0

# 权重：每点天赋的效果都很小。全树 44 点，点满大约 +20% 生命、+15% 伤害、
# -8% 冷却、+12% 范围，以及若干开局便利 —— 仍小于一整套局内被动的量级。
BRANCHES = (
    {
        "key": "endurance", "name": "深潜体魄", "summary": "血量、护甲与续航",
        "nodes": (
            {"key": "vitality", "name": "深潜体质", "detail": "最大生命 +4%",
             "max": 5, "cost": 1, "effect": {"max_hp": .04}},
            {"key": "pressure_shell", "name": "抗压壳", "detail": "受到伤害 -1",
             "max": 1, "cost": 3, "effect": {"armor": 1}},
            {"key": "self_heal", "name": "自愈膜", "detail": "每秒回复 +0.08",
             "max": 3, "cost": 2, "effect": {"recovery": .08}},
            {"key": "emergency_float", "name": "应急上浮", "detail": "开局 6 秒无敌",
             "max": 1, "cost": 3, "effect": {"start_invincible": 6}},
        ),
    },
    {
        "key": "firepower", "name": "过载火力", "summary": "伤害、冷却与范围",
        "nodes": (
            {"key": "overcharge", "name": "过载弹药", "detail": "所有武器伤害 +3%",
             "max": 5, "cost": 1, "effect": {"might": .03}},
            {"key": "quick_reload", "name": "快速装填", "detail": "武器冷却 -2%",
             "max": 4, "cost": 1, "effect": {"cooldown": -.02}},
            {"key": "sonar", "name": "声呐校准", "detail": "武器范围 +4%",
             "max": 3, "cost": 1, "effect": {"area": .04}},
            {"key": "prefit", "name": "预置改装", "detail": "初始武器 Lv.2",
             "max": 1, "cost": 4, "effect": {"start_weapon_level": 1}},
        ),
    },
    {
        "key": "salvage", "name": "打捞直觉", "summary": "拾取、幸运与经验",
        "nodes": (
            {"key": "beacon", "name": "信标磁锚", "detail": "拾取范围 +10%",
             "max": 3, "cost": 1, "effect": {"magnet": .10}},
            {"key": "lucky_coin", "name": "幸运锈币", "detail": "幸运 +4%",
             "max": 3, "cost": 1, "effect": {"luck": .04}},
            {"key": "salvage_license", "name": "打捞执照", "detail": "经验获取 +4%",
             "max": 3, "cost": 1, "effect": {"growth": .04}},
            {"key": "reserve", "name": "应急储备", "detail": "开局获得 150 打捞分",
             "max": 1, "cost": 2, "effect": {"start_salvage": 150}},
        ),
    },
)

NODES = {
    node["key"]: node
    for branch in BRANCHES
    for node in branch["nodes"]
}

AWARD_ITEMS = (
    {"key": "complete", "label": "完成试潜", "detail": "打完一局（中途离开不算）"},
    {"key": "win", "label": "回收成功", "detail": "全队坚持到作战结束"},
    {"key": "boss", "label": "击退守卫", "detail": "击败断缆守卫"},
    {"key": "survive", "label": "坚守到底", "detail": "结算时仍然存活"},
    {"key": "firepower", "label": "火力压制", "detail": "输出明显高于小队平均"},
    {"key": "rescue", "label": "救援专家", "detail": "救起队友至少 2 次"},
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_bonus() -> dict:
    return {
        "max_hp": 0.0, "might": 0.0, "cooldown": 0.0, "area": 0.0,
        "armor": 0.0, "recovery": 0.0, "magnet": 0.0, "luck": 0.0,
        "growth": 0.0, "start_weapon_level": 0, "start_invincible": 0.0,
        "start_salvage": 0,
    }


def bonuses(levels: dict) -> dict:
    """把已点等级换算成引擎可用的增量字典（纯函数，便于单测）。"""
    out = _empty_bonus()
    for key, level in (levels or {}).items():
        node = NODES.get(key)
        if not node or not isinstance(level, int) or level <= 0:
            continue
        for stat, amount in node["effect"].items():
            out[stat] += amount * level
    return out


def _spent(levels: dict) -> int:
    return sum(NODES[key]["cost"] * level for key, level in levels.items()
               if key in NODES and isinstance(level, int) and level > 0)


def _read_levels(raw: str) -> dict:
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def _profile(conn, user_id: int) -> dict:
    row = conn.execute(
        "SELECT points, earned, levels FROM abyss_talents WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    points = row["points"] if row else 0
    earned = row["earned"] if row else 0
    levels = _read_levels(row["levels"]) if row else {}
    tree = []
    for branch in BRANCHES:
        nodes = []
        for node in branch["nodes"]:
            rank = int(levels.get(node["key"], 0) or 0)
            nodes.append({
                "key": node["key"], "name": node["name"], "detail": node["detail"],
                "max": node["max"], "cost": node["cost"], "rank": rank,
                "affordable": rank < node["max"] and points >= node["cost"],
            })
        tree.append({**{k: branch[k] for k in ("key", "name", "summary")},
                     "nodes": nodes})
    return {
        "points": points, "earned": earned, "spent": _spent(levels),
        "levels": levels, "tree": tree,
        "rules": {
            "per_game_max": MAX_POINTS_PER_GAME,
            "active_kills": ACTIVE_KILLS,
            "active_damage": int(ACTIVE_DAMAGE),
            "items": list(AWARD_ITEMS),
            "note": "完成试潜得 1 点；达到 60 次击退或 300 点伤害后，"
                    "回收成功、击退守卫、坚守到底、火力压制、救援专家再各 +1，"
                    "单局最多 4 点。中途离开不结算。",
        },
    }


def profile(user_id: int) -> dict:
    conn = database.get_conn()
    try:
        return _profile(conn, user_id)
    finally:
        conn.close()


def bonus_for_user(user_id: int) -> dict:
    """开局时读取该用户的天赋增量；没有记录返回全零。"""
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT levels FROM abyss_talents WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    return bonuses(_read_levels(row["levels"]) if row else {})


def _ensure_row(conn, user_id: int):
    conn.execute(
        """INSERT INTO abyss_talents (user_id, points, earned, levels, updated_at)
           VALUES (?, 0, 0, '{}', ?)
           ON CONFLICT(user_id) DO NOTHING""",
        (user_id, _now()),
    )


def allocate(user_id: int, node_key: str) -> dict:
    """加一点。点数不足 / 已满级 / 节点不存在都抛 ValueError（路由翻成 400）。"""
    node = NODES.get(node_key)
    if not node:
        raise ValueError("没有这个天赋")
    conn = database.get_conn()
    try:
        conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        _ensure_row(conn, user_id)
        row = conn.execute(
            "SELECT points, levels FROM abyss_talents WHERE user_id = ?", (user_id,)
        ).fetchone()
        levels = _read_levels(row["levels"])
        rank = int(levels.get(node_key, 0) or 0)
        if rank >= node["max"]:
            raise ValueError("该天赋已满级")
        if row["points"] < node["cost"]:
            raise ValueError("天赋点不足")
        levels[node_key] = rank + 1
        conn.execute(
            "UPDATE abyss_talents SET points = ?, levels = ?, updated_at = ? "
            "WHERE user_id = ?",
            (row["points"] - node["cost"],
             json.dumps(levels, ensure_ascii=False), _now(), user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return profile(user_id)


def reset(user_id: int) -> dict:
    """免费重置：清空全部等级，把已花掉的点数还回钱包。"""
    conn = database.get_conn()
    try:
        conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        _ensure_row(conn, user_id)
        row = conn.execute(
            "SELECT earned FROM abyss_talents WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.execute(
            "UPDATE abyss_talents SET points = ?, levels = '{}', updated_at = ? "
            "WHERE user_id = ?",
            (row["earned"], _now(), user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return profile(user_id)


def _award_detail(gs: dict, player: dict, team_damage: float,
                  average_kills: float) -> list[dict]:
    detail = [{"key": "complete", "label": "完成试潜", "points": 1}]
    active = (player.get("kills", 0) >= ACTIVE_KILLS
              or player.get("damage_dealt", 0) >= ACTIVE_DAMAGE)
    if active:
        if gs.get("result") == "won":
            detail.append({"key": "win", "label": "回收成功", "points": 1})
        if gs.get("boss_dead"):
            detail.append({"key": "boss", "label": "击退守卫", "points": 1})
        if player.get("hp", 0) > 0:
            detail.append({"key": "survive", "label": "坚守到底", "points": 1})
        if gs.get("count", 1) > 1 and (
            player.get("damage_dealt", 0) >= team_damage * .30
            or player.get("kills", 0) >= average_kills * 1.30
        ):
            detail.append({"key": "firepower", "label": "火力压制", "points": 1})
        if player.get("rescues", 0) >= 2:
            detail.append({"key": "rescue", "label": "救援专家", "points": 1})
    return detail[:MAX_POINTS_PER_GAME]


def award_abyss(room: dict) -> dict[int, dict]:
    """结算发点，按「房间 + 开局时间」幂等。返回 {user_id: 奖励详情}。

    奖励详情同时写到 `gs["players"][uid]["talent_award"]`，随终局快照下发；
    同一局再次结算（玩家离开触发第二次 settle、热重启）读取第一次的记录，
    不会重复加点，也不会把已经展示过的详情变成 0。
    """
    gs = room.get("game_state")
    round_key = room.get("game_started_at") or room.get("created_at")
    if (not gs or not gs.get("result") or gs.get("result") == "abandoned"
            or room.get("practice") or not round_key):
        return {}
    members = [p for p in room.get("players", [])
               if not p.get("is_bot") and p["id"] > 0]
    game_players = gs.get("players", {})
    active = [game_players[p["id"]] for p in members
              if p["id"] in game_players and not game_players[p["id"]].get("departed")]
    if not active:
        return {}
    team_damage = sum(float(p.get("damage_dealt", 0)) for p in active)
    average_kills = sum(p.get("kills", 0) for p in active) / len(active)

    awarded: dict[int, dict] = {}
    conn = database.get_conn()
    try:
        conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        for member in members:
            player = game_players.get(member["id"])
            if not player or player.get("departed"):
                continue
            detail = _award_detail(gs, player, team_damage, average_kills)
            points = sum(item["points"] for item in detail)
            if points <= 0:
                continue
            cursor = conn.execute(
                """INSERT OR IGNORE INTO abyss_talent_awards
                   (user_id, room_code, round_key, points, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (member["id"], room["code"], round_key, points,
                 json.dumps(detail, ensure_ascii=False), _now()),
            )
            if cursor.rowcount:
                _ensure_row(conn, member["id"])
                conn.execute(
                    """UPDATE abyss_talents
                       SET points = points + ?, earned = earned + ?, updated_at = ?
                       WHERE user_id = ?""",
                    (points, points, _now(), member["id"]),
                )
        conn.commit()
        for member in members:
            row = conn.execute(
                """SELECT points, detail FROM abyss_talent_awards
                   WHERE user_id = ? AND room_code = ? AND round_key = ?""",
                (member["id"], room["code"], round_key),
            ).fetchone()
            if not row:
                continue
            balance = conn.execute(
                "SELECT points FROM abyss_talents WHERE user_id = ?",
                (member["id"],),
            ).fetchone()
            award = {
                "points": row["points"],
                "detail": json.loads(row["detail"]),
                "balance": balance["points"] if balance else row["points"],
            }
            awarded[member["id"]] = award
            player = game_players.get(member["id"])
            if player is not None:
                player["talent_award"] = award
    finally:
        conn.close()
    return awarded