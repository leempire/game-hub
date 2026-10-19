"""UNO 房规表与预设档位 —— **房规的唯一来源**（v1.14）。

为什么要这张表：UNO 的官方扩展方式本来就只有一种 —— 改一两条房规。Mattel 自己就是
这么做的（`JUMP-IN UNO` / `UNO TEAMS` / `SHOW 'EM NO MERCY` 各自只改一两条规则），
社区房规（叠加、7-0、摸到能出为止）也是同一形态。所以本作**不做「每个模式一份引擎」**，
而是「一份引擎 + 一张房规表 + 若干命名预设」—— 与军棋的明/暗棋、深空休眠的六种人机
性格是同一条做法（差异全在旋钮上，决策流程只有一份）。

三条纪律：

- **默认档必须与「经典 UNO」逐字节一致**：`PRESETS["classic"]` 就是本作 v1.9 以来的
  行为，靠 `test_uno.py` 原有的用例原样通过来保证（那些用例都不传 rules）。
- **客户端不抄这张表**：档位名、每条房规的中文名与说明都随房间数据下发 ——
  v1.15 起统一由 `room_settings.items_for()` 生成 `room.settings`（本模块的
  `RULE_META` 就是那 7 条房规那一半的来源，另加一个 `preset` 档位项）。
  客户端只按 `kind` 渲染一个通用表单，一行规则都不写。
- **引擎不认识时间**：每条房规都必须能由「当前状态 + 这一步动作」判定完，不许引入
  墙钟或 `asyncio.sleep`（同深空休眠「引擎只认识 `dt_ms`」那条纪律）。UNO 现在没有
  tick，别为了任何一条房规引入 tick。

调研与取舍见 `docs/游戏设计/UNO/增强调研.md`。
"""

# ── 取值域 ──────────────────────────────────────────────────


# 叠牌口径。**改这里必须同时看 `uno.can_answer_penalty` 的分支**。
STACK_MODES = ("off", "same", "upgrade")
# 7 换手 / 0 传手。
SEVEN_ZERO_MODES = ("off", "swap", "rotate", "both")
# 牌堆。`no_mercy` 是 Show 'em No Mercy 那副（164 张，多 5 种功能牌）。
DECK_KINDS = ("classic", "no_mercy")
# 组队。`2v2` 是 Mattel 的 UNO Teams 口径（**恰好 4 人**，对家是队友）。
TEAM_MODES = ("off", "2v2")


# ── 房规定义（服务端下发的中文名与说明） ─────────────────────
#
# 形状是给客户端渲染**通用表单**用的：`kind="choice"` 画一排芯片、
# `kind="toggle"` 画一个开关。客户端不认识任何 `key` 的语义。

RULE_META: list[dict] = [
    {
        "key": "stack_mode",
        "label": "叠牌",
        "kind": "choice",
        "blurb": "被罚牌时能不能把罚牌转嫁给下家。",
        "choices": [
            {"value": "off", "label": "不叠",
             "blurb": "被 +2 只能摸牌，不能转嫁。"},
            {"value": "same", "label": "同型叠加",
             "blurb": "被 +2 可以用 +2 转嫁，被 +4 只能用 +4 转嫁。"},
            {"value": "upgrade", "label": "等值或更高",
             "blurb": "被 +2 可以用 +2 或 +4 转嫁 —— 罚牌会越滚越大。"},
        ],
    },
    {
        "key": "draw_until_play",
        "label": "摸到能出为止",
        "kind": "toggle",
        "blurb": "没牌可出时一直摸，直到摸出能打的那张（经典规则只摸一张）。"
                 "手牌会涨得很快。",
    },
    {
        "key": "must_play",
        "label": "有牌必出",
        "kind": "toggle",
        "blurb": "手里有能打的牌就必须打，不能靠摸牌拖延。",
    },
    {
        "key": "jump_in",
        "label": "抢出",
        "kind": "toggle",
        "blurb": "手里有与牌堆顶**完全相同**的牌（同色同数字）时，不管轮到谁都能"
                 "立刻打出去，回合从你继续。",
    },
    {
        "key": "seven_zero",
        "label": "7 换手 / 0 传手",
        "kind": "choice",
        "blurb": "打出 7 时和一名玩家交换全部手牌；打出 0 时所有人按当前方向把手牌"
                 "传给下家。",
        "choices": [
            {"value": "off", "label": "不启用", "blurb": "7 和 0 只是普通数字牌。"},
            {"value": "swap", "label": "只换手",
             "blurb": "打出 7 时选一名玩家交换手牌。"},
            {"value": "rotate", "label": "只传手",
             "blurb": "打出 0 时所有人把手牌传给下家。"},
            {"value": "both", "label": "两个都要",
             "blurb": "7 换手、0 传手都启用 —— 手牌会频繁整体易主。"},
        ],
    },
    {
        "key": "deck",
        "label": "牌堆",
        "kind": "choice",
        "blurb": "用哪一副牌。",
        "choices": [
            {"value": "classic", "label": "标准",
             "blurb": "108 张，0-9 数字牌 + 跳过 / 反转 / +2 / 变色 / 变色+4。"},
            {"value": "no_mercy", "label": "无怜悯",
             "blurb": "164 张，多出「跳过所有人」「弃同色」「变色+6」「变色+10」"
                      "「变色反转+4」五种狠牌，功能牌占了多数。"},
        ],
    },
    {
        "key": "mercy",
        "label": "25 张淘汰",
        "kind": "toggle",
        "blurb": "手牌涨到 25 张就**直接出局**（罚牌叠起来很容易到这个数）。"
                 "别人都被淘汰掉、只剩你一个时，你获胜。",
    },
    {
        "key": "teams",
        "label": "组队",
        "kind": "choice",
        "blurb": "四人分两队，对家是队友。",
        "choices": [
            {"value": "off", "label": "各自为战",
             "blurb": "经典的四人对局，谁先出完手牌谁赢。"},
            {"value": "2v2", "label": "2v2 组队",
             "blurb": "**恰好 4 人**：座位 0/2 一队、1/3 一队（对家是队友），"
                      "一队**两人都出完手牌**才算赢 —— 一个人把牌倒干净就跑，"
                      "等于把队友一个人丢在场上。"},
        ],
    },
]

# key → 默认值。**这就是「经典 UNO」**。
DEFAULTS: dict = {
    "stack_mode": "same",
    "draw_until_play": False,
    "must_play": False,
    "jump_in": False,
    "seven_zero": "off",
    "deck": "classic",
    "mercy": False,
    "teams": "off",
}

# 取值域（校验用）。没有列的键按 bool 处理。
_CHOICES = {
    "stack_mode": STACK_MODES,
    "seven_zero": SEVEN_ZERO_MODES,
    "deck": DECK_KINDS,
    "teams": TEAM_MODES,
}


# ── 预设档位 ────────────────────────────────────────────────

PRESETS: dict[str, dict] = {
    "classic": {
        "name": "经典",
        "blurb": "标准的 UNO：+2 只能叠 +2，第一个出完手牌的人获胜。",
        "rules": dict(DEFAULTS),
    },
    "party": {
        "name": "热闹",
        "blurb": "抢出 + 7 换手 + 摸到能出为止 + 有牌必出 + 罚牌可越叠越大 —— "
                 "牌权频繁易手，节奏最快。",
        "rules": {
            **DEFAULTS,
            "stack_mode": "upgrade",
            "draw_until_play": True,
            "must_play": True,
            "jump_in": True,
            "seven_zero": "both",
        },
    },
    "team": {
        "name": "组队",
        "blurb": "2v2：座位 0/2 一队、1/3 一队（对家是队友），一队两人都出完手牌"
                 "才算赢 —— 把牌倒干净就跑等于把队友丢在场上。**恰好 4 人**。",
        "rules": {**DEFAULTS, "teams": "2v2"},
    },
    "no_mercy": {
        "name": "无怜悯",
        "blurb": "164 张的狠牌堆：罚牌能叠到 +10、摸到能出为止、7 换手 / 0 传手，"
                 "手牌涨到 25 张直接出局。要么出完，要么熬到只剩你一个。",
        "rules": {
            **DEFAULTS,
            "stack_mode": "upgrade",
            "draw_until_play": True,
            "seven_zero": "both",
            "deck": "no_mercy",
            "mercy": True,
        },
    },
}

# 「自定义」**不是一个可选档位，而是一个状态**：房主逐条改过开关、改到不匹配任何
# 预设时，`uno_preset` 就落到这里。它不出现在 PRESETS 里（没有固定取值），也不在
# `preset_choices()` 里下发 —— 大厅里那几行开关本身就是「自定义」的界面，
# 再放一个「自定义」芯片等于让房主点一个什么都不会发生的东西。
CUSTOM_ID = "custom"

PRESET_IDS = tuple(PRESETS)


def preset_choices() -> list[dict]:
    """下发给客户端的大厅档位表（服务端是唯一来源，客户端不抄）。

    芯片只负责**一键填好整份房规**；下面那几行开关随时可改，改完由 `preset_of`
    反查该高亮哪个芯片（都不匹配就一个都不亮 = 自定义）。
    """
    return [{"id": pid, "name": p["name"], "blurb": p["blurb"]}
            for pid, p in PRESETS.items()]


def rule_meta() -> list[dict]:
    """下发给客户端的房规定义（客户端按 `kind` 渲染通用表单）。"""
    return RULE_META


# ── 校验与解析 ──────────────────────────────────────────────


def normalize(rules: dict | None) -> dict:
    """把一份（可能残缺、可能带脏值的）房规补全成**完整且合法**的一份。

    未知的键与非法取值一律**抛错**，不静默忽略 —— 静默忽略意味着房主点了芯片、
    界面上却什么也没变（本项目反复踩过「被静默拒绝的动作在界面上就是没反应」）。
    """
    out = dict(DEFAULTS)
    if not rules:
        return out
    for key, value in rules.items():
        if key not in DEFAULTS:
            raise ValueError(f"没有这条房规：{key}")
        if key in _CHOICES:
            if value not in _CHOICES[key]:
                raise ValueError(f"「{_label_of(key)}」没有这个取值")
        elif not isinstance(value, bool):
            raise ValueError(f"「{_label_of(key)}」的取值不对")
        out[key] = value
    return out


def rules_of_preset(preset_id: str) -> dict:
    """档位 id → 房规。**只认 `PRESETS` 里真实存在的档位**（`custom` 不是档位）。"""
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise ValueError("没有这个玩法档位")
    return dict(preset["rules"])


def is_valid_preset(preset_id) -> bool:
    return preset_id in PRESET_IDS


def preset_name(rules: dict) -> str:
    """一份房规的**中文档位名**（不匹配任何预设时是「自定义」）。

    给战绩快照用：同一个 UNO 在「热闹」档和「经典」档下完全是两种牌局，战绩里只写
    一个游戏名等于什么都没说。**名字在这里就翻译好**，客户端不必为了显示它去抄一份
    档位表（同「游戏名不散在多处」那条纪律）。
    """
    pid = preset_of(rules)
    preset = PRESETS.get(pid)
    return preset["name"] if preset else "自定义"


def preset_of(rules: dict) -> str:
    """反查一份房规落在哪个预设上；都不匹配就是 `custom`。

    大厅的档位芯片靠它高亮 —— 房主手动把开关改回经典那一组时，「经典」会自己亮
    回来（不藏状态：界面显示的就是真实生效的那一份房规）。
    """
    for pid, preset in PRESETS.items():
        if preset["rules"] == rules:
            return pid
    return CUSTOM_ID


def _label_of(key: str) -> str:
    for meta in RULE_META:
        if meta["key"] == key:
            return meta["label"]
    return key
