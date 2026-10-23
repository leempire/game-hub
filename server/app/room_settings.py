"""房间级设置的**唯一注册表**（v1.15）—— 「这个房间能调什么」的唯一契约。

## 为什么要有这个模块

在这之前，五处房间级设置（扫雷难度 / 军棋玩法 / 深空休眠地图 / 海战棋棋盘 / UNO 房规）
只是「约定俗成地同构」：五个命名不一致的 setter、五条 ws 消息、五段几乎一样的 handler、
`room_to_dict` 里五个 `if room["game"] == …` 分支，而且**可选项列表的形状还有四种**。
更糟的是扫雷与军棋的档位表**压根不下发**，客户端各抄了一份（军棋直接写死在 JSX 里）——
这正好违反本项目一直守的那条纪律：**档位表由服务端下发，客户端不自己抄一份**
（同象棋的 `legal`、飞行棋的 `path`、俄罗斯方块的 `shapes`、UNO 的 `playable`）。

现在只有这一张表。客户端按 `kind` 通用渲染，不认识任何 `key` 的语义 ——
**加一条房规，客户端零改动**（UNO 那 7 条就是遍历 `uno_rules.RULE_META` 生成的）。

## 两条设计约束

- **`store` 指向的房间键一律不动**（`room["difficulty"]` / `room["uno_rules"]` …）。
  统一的是**对外协议**，不是把引擎也翻一遍：`create_room` 与五个 `_start_*` 的读取
  路径一行都不用改，回归风险因此小得多。
- **不 import `room_manager`**（只被它 import），否则循环依赖。与 `uno_rules.py` 同层：
  纯数据 + 纯函数，没有 IO。

## 下发形状

`room.settings` 是一个**扁平数组**，每项：

    {key, label, kind, value, value_label, blurb, choices?, visible_when?, in_summary?}

`value_label` 与 `blurb` 都由服务端翻译好 —— 客户端不必为了显示「中级 / 自定义」
去抄一份表，也不会有 UNO 的 `custom` 那种「不在 choices 里所以查不到名字」的落空。
"""

from dataclasses import dataclass
from typing import Callable

from app.game import battleship, minesweeper, stasis as stasis_engine, stasis_map, uno_rules

KIND_CHOICE = "choice"
KIND_TOGGLE = "toggle"

# toggle 的默认中文（开 / 关）。个别设置有自己更贴切的说法，见军棋的「阵亡公开身份」。
DEFAULT_TOGGLE_LABELS = ("开", "关")


@dataclass(frozen=True)
class Setting:
    """一条房间级设置。

    `store` 是它在房间字典里真正存值的键（`None` = 派生值，只有 UNO 的 `preset` 是）；
    `read` / `apply` 缺省就是「读 / 写 `room[store]`」，只有派生值才需要自己给。
    """

    key: str
    label: str
    kind: str
    store: str | None = None
    default: object = None
    blurb: str = ""
    choices: tuple = ()
    #: 当前值**不在** `choices` 里时显示什么（UNO 的 `custom` → 「自定义」）。
    fallback_label: str = ""
    toggle_labels: tuple = DEFAULT_TOGGLE_LABELS
    #: 只在另一项取某个值时才显示。**由客户端求值**（见下面的 `visible_when` 说明）。
    visible_when: dict | None = None
    in_summary: bool = False
    read: Callable[[dict], object] | None = None
    apply: Callable[[dict, object], None] | None = None
    value_label: Callable[[dict, object], str] | None = None


# ── 各游戏的设置 ────────────────────────────────────────────


def _mine_settings() -> list[Setting]:
    return [
        Setting(
            key="difficulty", label="难度", kind=KIND_CHOICE,
            store="difficulty", default="intermediate", in_summary=True,
            choices=tuple(
                {"value": d, "label": minesweeper.DIFFICULTY_LABELS[d],
                 "blurb": minesweeper.difficulty_blurb(d)}
                for d in minesweeper.DIFFICULTIES
            ),
        ),
        Setting(
            key="daily", label="每日固定盘", kind=KIND_TOGGLE,
            store="mine_daily", default=False, in_summary=True,
            toggle_labels=("今日盘", "随机盘"),
            blurb="开启后雷盘按 UTC 日期定种子：同一天同一难度，所有人拿到同一份盘，"
                  "适合比最佳用时。第一下翻开的 3×3 安全区仍随落点移动。",
        ),
    ]


def _stasis_settings() -> list[Setting]:
    return [
        Setting(
            key="map", label="地图", kind=KIND_CHOICE,
            store="stasis_map_id", default=stasis_map.DEFAULT_MAP_ID, in_summary=True,
            choices=tuple(
                {"value": mid, "label": spec.name, "blurb": spec.blurb}
                for mid, spec in stasis_map.SPECS.items()
            ),
        ),
        Setting(
            key="difficulty", label="难度", kind=KIND_CHOICE,
            store="stasis_difficulty", default=stasis_engine.DEFAULT_DIFFICULTY,
            in_summary=True,
            choices=tuple(
                {"value": key, "label": spec["label"], "blurb": spec["blurb"]}
                for key, spec in stasis_engine.DIFFICULTY_SPECS.items()
            ),
        ),
    ]


TETRIS_START_LEVEL = 1


def _tetris_settings() -> list[Setting]:
    """起始速度档：只给三档，不做自由滑条。

    档位同时抬高分倍率与重力速度（引擎 `level_of(lines, start_level)`），
    同一房间里所有人同档，所以排名公平。三档就是引擎 `gravityMs` 表的
    第 1 / 5 / 10 行，客户端不需要为「起始速度」再写一套映射。
    """
    return [Setting(
        key="start_level", label="起始速度", kind=KIND_CHOICE,
        store="tetris_start_level", default=TETRIS_START_LEVEL, in_summary=True,
        choices=(
            {"value": 1, "label": "慢", "blurb": "从 1 级起步，方块下落最慢，适合熟悉操作。"},
            {"value": 5, "label": "标准", "blurb": "从 5 级起步，下落明显更快，分数倍率也高。"},
            {"value": 10, "label": "快", "blurb": "从 10 级起步，方块几乎贴地掉；高手档。"},
        ),
        value_label=lambda room, value: f"{value} 级",
    )]


def _bs_label(size: int) -> str:
    """「标准 · 10×10」—— 尺寸写出来比只写「标准」有用（大厅里看得见盘多大）。"""
    preset = battleship.SIZE_PRESETS.get(size)
    return f"{preset['name']} · {size}×{size}" if preset else f"{size}×{size}"


def _battleship_settings() -> list[Setting]:
    return [
        Setting(
            key="board_size", label="棋盘", kind=KIND_CHOICE,
            store="bs_size", default=battleship.DEFAULT_SIZE, in_summary=True,
            choices=tuple(
                {"value": n, "label": p["name"], "blurb": p["blurb"]}
                for n, p in battleship.SIZE_PRESETS.items()
            ),
            value_label=lambda room, value: _bs_label(value),
        ),
        Setting(
            key="adjacent_placement", label="舰船不可相邻", kind=KIND_TOGGLE,
            store="bs_adjacent", default=False, in_summary=True,
            blurb="开启后舰船之间必须留出一圈水（含斜角），布阵更讲究掩护与走位；"
                  "人机的随机布阵也会遵守。",
        ),
        Setting(
            key="hide_sunk_ship_type", label="击沉不报舰种", kind=KIND_TOGGLE,
            store="bs_hide_sunk_type", default=False, in_summary=True,
            blurb="开启后击沉只公开船体位置，不告诉你沉的是哪一艘——"
                  "得靠剩余舰种与武器挂靠自己推。",
        ),
    ]


def _luzhanqi_settings() -> list[Setting]:
    return [
        Setting(
            key="mode", label="玩法", kind=KIND_CHOICE,
            store="luzhanqi_mode", default="open", in_summary=True,
            choices=(
                {"value": "open", "label": "明棋",
                 "blurb": "双方棋子全部公开，纯拼算路与子力交换。"},
                {"value": "hidden", "label": "暗棋",
                 "blurb": "只看得到自己的棋子，对方的扣着。碰子只告诉你「谁出局」，"
                          "靠交战结果反推对方阵型。"},
            ),
        ),
        Setting(
            key="reveal", label="阵亡公开身份", kind=KIND_TOGGLE,
            store="luzhanqi_reveal", default=True, in_summary=True,
            toggle_labels=("公开", "不公开"),
            blurb="暗棋下，棋子阵亡时把它的身份公开给对手；关掉的话裁判只宣布「谁出局」。",
            # **只在暗棋下显示**。常显方案被否掉：明棋下点它界面上什么都不会变
            # （`luzhanqi_reveal` 只在 `hidden` 下被 `create_game` 读），正是本项目
            # 反复栽的「点了没反应」；要补救就得置灰，而禁用态在本项目被明文禁止
            # （RNW 下 `disabled` = `pointer-events:none`，点了连提示都没有）。
            #
            # **过滤放在客户端**：`mode` 的值就在同一个数组里，一次遍历就能算；
            # 服务端若先过滤，房间页那行摘要还得再过滤一遍，两边口径容易漂。
            visible_when={"key": "mode", "value": "hidden"},
        ),
    ]


def _apply_uno_preset(room: dict, value) -> None:
    """写 `preset` = **整份重置房规**（这是它与其他设置唯一不同的地方）。"""
    room["uno_rules"] = uno_rules.rules_of_preset(value)


def _apply_uno_rule(key: str):
    """写某一条房规 = 只改这一个键，其余原样（「只合并给出的键」是结构自带的）。"""
    def _apply(room: dict, value) -> None:
        rules = uno_rules.normalize(room.get("uno_rules"))
        rules[key] = value
        room["uno_rules"] = rules
    return _apply


def _uno_preset_setting() -> Setting:
    return Setting(
        key="preset", label="玩法档位", kind=KIND_CHOICE, in_summary=True,
        # `custom` 不是档位、是「当前这套不完全是任何一个预设」这个状态，所以它
        # **不在 choices 里**（放一个点了什么都不会发生的芯片是本项目明确反对的），
        # 只在 `fallback_label` 里露个面。
        fallback_label="自定义",
        blurb="逐条挑过房规，当前这套不完全是任何一个预设。",
        choices=tuple(
            {"value": p["id"], "label": p["name"], "blurb": p["blurb"]}
            for p in uno_rules.preset_choices()
        ),
        read=lambda room: uno_rules.preset_of(
            uno_rules.normalize(room.get("uno_rules"))),
        apply=_apply_uno_preset,
    )


def _uno_rule_settings() -> list[Setting]:
    """7 条房规由 `RULE_META` **生成** —— 以后加一条房规，客户端零改动。"""
    out = []
    for meta in uno_rules.RULE_META:
        out.append(Setting(
            key=meta["key"], label=meta["label"], kind=meta["kind"],
            blurb=meta["blurb"], choices=tuple(meta.get("choices") or ()),
            read=(lambda room, k=meta["key"]: uno_rules.normalize(
                room.get("uno_rules"))[k]),
            apply=_apply_uno_rule(meta["key"]),
        ))
    return out


_GAMES: dict[str, list[Setting]] = {
    "minesweeper": _mine_settings(),
    "luzhanqi": _luzhanqi_settings(),
    "stasis": _stasis_settings(),
    "battleship": _battleship_settings(),
    "tetris": _tetris_settings(),
    "uno": [_uno_preset_setting(), *_uno_rule_settings()],
}


# ── 对外 API ────────────────────────────────────────────────


def specs_for(game: str) -> list[Setting]:
    """某个游戏的全部设置（没有就是空表 —— 调用方据此决定要不要显示入口）。"""
    return _GAMES.get(game, [])


def spec_for(game: str, key: str) -> Setting | None:
    """按游戏 + 线上键查一条设置。

    **「游戏匹配」这条校验由它结构性保证**：在 UNO 房里查 `difficulty` 必然查不到，
    于是回到「这个房间没有这项设置」。老代码里 `set_battleship_size` 恰恰漏了这道
    校验（在 UNO 房里也能改海战棋尺寸）。
    """
    for spec in specs_for(game):
        if spec.key == key:
            return spec
    return None


def value_of(spec: Setting, room: dict):
    """这一项当前生效的值。

    **`None` 一律当成「还没设过」退回默认值** —— 深空休眠的地图就是这样：`create_room`
    刻意写的是 `room["stasis_map_id"] = None`（默认图由引擎自己兜底），
    `room.get(store, default)` 那种写法在这里取不到默认值，会让大厅显示成空的。
    """
    if spec.read is not None:
        return spec.read(room)
    value = room.get(spec.store)
    return spec.default if value is None else value


def coerce(spec: Setting, value):
    """把客户端传来的值校验成合法取值，非法一律**抛中文错误**。

    静默忽略是不行的：房主点了芯片、界面上却什么也没变，在本项目就是「点了没反应」。
    """
    if spec.kind == KIND_TOGGLE:
        if not isinstance(value, bool):
            raise ValueError(f"「{spec.label}」只能是开或关")
        return value
    # `True == 1` 在 Python 里成立，得先把它挡掉，否则开关能冒充数字档位。
    if isinstance(value, bool):
        raise ValueError(f"「{spec.label}」没有这个取值")
    for choice in spec.choices:
        if choice["value"] == value:
            return value
    raise ValueError(f"「{spec.label}」没有这个取值")


def apply(room: dict, spec: Setting, value) -> None:
    """写进房间（调用方负责先 `coerce`）。"""
    if spec.apply is not None:
        spec.apply(room, value)
    else:
        room[spec.store] = value


def label_of(spec: Setting, room: dict, value) -> str:
    """当前值的中文名（摘要与只读视图都用它）。"""
    if spec.value_label is not None:
        return spec.value_label(room, value)
    if spec.kind == KIND_TOGGLE:
        return spec.toggle_labels[0] if value else spec.toggle_labels[1]
    for choice in spec.choices:
        if choice["value"] == value:
            return choice["label"]
    return spec.fallback_label or str(value)


def items_for(game: str, room: dict) -> list[dict]:
    """`room.settings` 的完整载荷（客户端直接渲染，不认识任何 key 的语义）。"""
    return [_item_json(spec, room) for spec in specs_for(game)]


def _item_json(spec: Setting, room: dict) -> dict:
    value = value_of(spec, room)
    item = {
        "key": spec.key,
        "label": spec.label,
        "kind": spec.kind,
        "value": value,
        "value_label": label_of(spec, room, value),
        "blurb": spec.blurb,
    }
    if spec.choices:
        item["choices"] = [dict(c) for c in spec.choices]
    if spec.kind == KIND_TOGGLE:
        # 开关也按「一排芯片」渲染（两个状态各一枚），所以两个状态的中文名都要给 ——
        # 否则客户端只能写死「开 / 关」，而军棋那条叫「公开 / 不公开」。
        item["toggle_labels"] = list(spec.toggle_labels)
    if spec.visible_when:
        item["visible_when"] = dict(spec.visible_when)
    if spec.in_summary:
        item["in_summary"] = True
    return item
