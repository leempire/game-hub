"""UNO (UNO 卡牌) pure game logic — no I/O, easy to unit-test.

2-4 人回合制牌类。本项目第一个非棋盘游戏，三条设计决策：

- **牌用字符串编码**（`R5` / `RS` / `R+2` / `W` / `W+4`）：JSON 友好，比较、断言、
  落盘都省事，不需要为「一张牌」定义对象。`card[0]` 是颜色、`card[1:]` 是牌面
  （`"W"` 的颜色是 W、牌面是空串）。
- **开局就把 108 张牌实体化并洗好**：之后全是具体牌列表，不存在「边玩边出」的
  随机源 —— 所以**不需要持久化 rng**（2048 存 `__rng__` 是因为它边玩边出块）。
  抽牌堆中途耗尽时才把弃牌堆洗回来（那时谁也不知道顺序，无所谓确定性）。
- **手牌是私有数据**：`game_to_sync(game, user_id)` 只下发本人的手牌，别人的只给
  张数 —— 真实牌局里你也只看得到对手手里有几张。
- **可出判定只有一份实现**（`playable_cards`）：服务端把它随状态下发给客户端做
  高亮，客户端不重写规则。象棋的合法着法、飞行棋的走子路径、俄罗斯方块的方块
  形状都是同一条教训 —— 多一份实现必然漂移。

房规（v1.14 起**由 `uno_rules` 这张表驱动**，不再是写死的常量）：
- 2-4 人
- **叠牌口径**（`stack_mode`）：不叠 / 同型叠加（默认，本作 v1.9 以来的行为）/
  等值或更高（No Mercy 口径）
- **摸到能出为止**（`draw_until_play`）：默认关（经典只摸一张）
- **有牌必出**（`must_play`）：默认关
- **抢出**（`jump_in`）：默认关
- **7 换手 / 0 传手**（`seven_zero`）：默认关
- **单局定胜负**：第一个出完手牌即终局（不做「累计到 500 分」的多局赛制）
- **有喊 UNO、不抓罚**：纯广播 + 徽标，不做「没喊被抓罚摸 2 张」的仲裁
- 断线玩家轮到他时由服务端**自动摸一张并过牌**（`auto_pass`），不让整桌卡到
  120s 保座超时

**默认档 = 经典 UNO**，与 v1.9 以来的行为逐字节一致（`uno_rules.DEFAULTS`）。
一处刻意的简化（仍然保留）：**+4 恒可出**（官方要求「手里没有当前颜色的牌才能出
+4」，多数数字化实现都不校验）。写进 docs/游戏设计/UNO/设计.md 的「已知取舍」。

**引擎不认识时间**：所有房规都由「当前状态 + 这一步动作」判定完，不碰墙钟、不开
tick（同深空休眠「引擎只认识 `dt_ms`」那条纪律）。抢出与 7 换手的「窗口」都是
**事件窗口**（某一步动作之前/之后），不是「N 秒之内」。
"""

import random
from collections import Counter

from app import uno_rules

# 牌面颜色（黑牌用 W，出牌后由出牌者指定四色之一）
COLORS = ["R", "G", "B", "Y"]
WILD_COLOR = "W"
# 牌面符号。数字牌用 "0".."9"，功能牌是下面这几个，黑牌另有无色的 "" 与 "+4" 等。
SKIP = "S"
REVERSE = "R"
DRAW2 = "+2"
WILD4 = "+4"
SYMBOLS = (SKIP, REVERSE, DRAW2)
# No Mercy 牌堆专属（`deck = "no_mercy"`）。前两个是**有色**牌，后三个是黑牌。
SKIP_ALL = "SE"        # 跳过所有人，然后自己再来一轮
DISCARD_ALL = "DA"     # 弃掉手里所有同色牌（弃掉的那些不结算效果）
DRAW6 = "+6"
DRAW10 = "+10"
REV_DRAW4 = "+RD4"     # 变色反转 +4：转向后那一家摸 4 并停一轮
ROULETTE = "+RO"       # 颜色轮盘：**下家点名一个颜色**，然后一直翻牌到出现该色
SYMBOLS_NO_MERCY = SYMBOLS + (SKIP_ALL, DISCARD_ALL)
# 会让下家摸牌的牌面（含黑牌）→ **罚几张**。
#
# `pending_kind` 存的就是**牌面本身**（`"+2"` / `"+4"` / …），不另设一套标记 ——
# 少一张映射表就少一处漂移（`same` 档比它、`upgrade` 档比 `PENALTY_VALUE`）。
PENALTY_VALUE = {DRAW2: 2, WILD4: 4, DRAW6: 6, DRAW10: 10, REV_DRAW4: 4}
# 罚牌牌面 → `effect` 文案键（客户端据此说「累计罚 N 张」/「转向后罚 4」）。
_EFFECT_OF_PENALTY = {DRAW2: "draw2", WILD4: "wild4", DRAW6: "draw6",
                      DRAW10: "draw10", REV_DRAW4: "rev_draw4"}
#: 组队档专属的两张黑牌（Mattel `UNO TEAMS`，官方说明书 JLG93）。它们是**变色牌**
#: （出牌时选色），只在 `teams=2v2` 时进牌堆。
TEAM_PLAY = "WTP"      # Wild Team Play：选色 → 回合**直接跳到队友**
TEAM_DRAW4 = "WTD4"    # Wild Team Draw Four：下家摸 2 停一轮，**他的队友也摸 2**
TEAM_CARDS = (TEAM_PLAY, TEAM_DRAW4)
#: 它们的**牌面**（`card_kind` 的取值）。`WILD_NEEDS_COLOR` 存的一律是牌面 ——
#: 纯变色牌是 `""`、`W+4` 是 `"+4"`，别往那张表里塞整张牌码。
TEAM_CARD_KINDS = tuple(c[1:] for c in TEAM_CARDS)
#: 每张几张。⚠️ **官方只写了效果、没写张数**（同 No Mercy 那 56 张的处境），
#: 这是本作定的。取 2 的理由：两张都是**强力且单向**的（传回合只帮队友、
#: 罚四只打对手一整队），4 张会让组队档退化成「谁摸到这两张谁赢」。
TEAM_CARD_COPIES = 2

# 出牌时**要指定颜色**的牌面。除纯变色牌 `""` 之外都是黑牌，**唯独颜色轮盘不是** ——
# 它的颜色由**下家**点名（见 `resolve_roulette`），所以出牌时不需要 `chosen_color`。
WILD_NEEDS_COLOR = ("", WILD4, DRAW6, DRAW10, REV_DRAW4) + TEAM_CARD_KINDS
# 「25 张淘汰」的阈值。**官方就是一个固定数**（Show 'em No Mercy 的 Mercy 规则），
# 没有需要调的档位，所以房规里只有一个开关 `mercy`，不开就没有这条规则。
MERCY_LIMIT = 25

# 终结原因
REASON_EMPTIED = "emptied"           # 有人出完了手牌
REASON_LAST_REMAINING = "last_remaining"  # 只剩一个人还在局中（其余认输/掉线判负）
REASON_TEAM_EMPTIED = "team_emptied"  # 组队档：一队两人都出完了手牌

# 组队档（`teams=2v2`）。**恰好 4 人**，对家是队友。
TEAMS_2V2 = "2v2"
TEAM_PLAYERS = 4

HAND_SIZE = 7
# 每色牌数：0 一张 + 1-9 各两张 + 跳/转/+2 各两张 = 25；四色 100 + 黑牌 8 = 108
DECK_SIZE = 108

# 玩家身份色（**不是**牌色）。牌色是 UNO 的游戏数据（红/绿/蓝/黄），玩家色只是
# 对手条上的身份标识 —— 顶层用 theme.ts 的 playerColors，与 unoColors 显式分开，
# 否则「红方玩家」和「红牌」在界面上会混成一件事。
PLAYER_COLORS = ["blue", "red", "green", "yellow"]


# ── 牌面工具 ────────────────────────────────────────────────


def card_color(card: str) -> str:
    """牌的颜色：`R`/`G`/`B`/`Y`/`W`。"""
    return card[0]


def card_kind(card: str) -> str:
    """牌的牌面：`"0"`..`"9"` / `"S"` / `"R"` / `"+2"` / `""`(变色) / `"+4"`。"""
    return card[1:]


def is_wild(card: str) -> bool:
    return card[0] == WILD_COLOR


def card_points(card: str) -> int:
    """计分用点数：数字牌按面值，功能牌 20，黑牌 50（标准 UNO）。

    按「是不是黑牌」分流，而不是查一张功能牌清单 —— 加新牌时不必回来补表。
    """
    if is_wild(card):
        return 50
    kind = card_kind(card)
    return int(kind) if kind.isdigit() else 20


def hand_points(hand: list[str]) -> int:
    return sum(card_points(c) for c in hand)


def build_deck(deck_kind: str = "classic", teams: bool = False) -> list[str]:
    """标准 108 张：0 每色 1 张、1-9 每色 2 张、跳/转/+2 每色 2 张、黑牌各 4 张。

    `deck_kind="no_mercy"` 是 Show 'em No Mercy 那副（**164 张**，多 56 张）。
    ⚠️ **那 56 张的配额是本作自己定的**：来源只给了牌名与效果，没给张数
    （`增强调研.md` 的 §7 里记着这件事）。取值理由是「让功能牌占多数」——
    原版的卖点就是「更狠」，一刀一刀磨的局在这里不成立。
    """
    cards: list[str] = []
    for color in COLORS:
        cards.append(color + "0")
        for n in "123456789":
            cards.append(color + n)
            cards.append(color + n)
        for sym in SYMBOLS:
            cards.append(color + sym)
            cards.append(color + sym)
    cards.extend([WILD_COLOR] * 4)
    cards.extend([WILD_COLOR + WILD4] * 4)
    if deck_kind == "no_mercy":
        cards += _no_mercy_extras()
    if teams:
        # 组队档的两张专属黑牌**加在整副牌之上**（与 `deck` 档位正交）——
        # 组队 + 无怜悯也是合法组合，那时是 164 + 4 张。
        for c in TEAM_CARDS:
            cards.extend([c] * TEAM_CARD_COPIES)
    return cards


def _no_mercy_extras() -> list[str]:
    """No Mercy 多出来的 56 张（配额见 `build_deck` 的注释）。"""
    extra: list[str] = []
    for color in COLORS:
        extra += [color + SKIP_ALL] * 2        # 8
        extra += [color + DISCARD_ALL] * 3     # 12
        extra += [color + DRAW2]               # 4（罚牌更密，叠加才有戏）
        extra += [color + SKIP]                # 4
        extra += [color + REVERSE]             # 4
    extra += [WILD_COLOR + WILD4] * 4          # 4
    extra += [WILD_COLOR + DRAW6] * 6          # 6
    extra += [WILD_COLOR + DRAW10] * 4         # 4
    extra += [WILD_COLOR + REV_DRAW4] * 6      # 6
    extra += [WILD_COLOR + ROULETTE] * 4       # 4
    assert len(extra) == 56, f"No Mercy 扩展牌应为 56 张，实际 {len(extra)}"
    return extra                               # 8+12+4+4+4 + 4+6+4+6+4 = 56 ✓


# ── 建局 ────────────────────────────────────────────────────


def create_game(user_ids: list[int], rules: dict | None = None) -> dict:
    """建一局：洗牌、每人发 7 张、翻一张数字牌作起始牌。

    `user_ids` 的顺序就是座位顺序（seat 0 先手）。`seat_uids[seat] == user_id`。
    `rules` 是房规（见 `uno_rules`）；**不传就是经典 UNO**，与 v1.9 以来的行为一致。
    单测不要依赖这里的洗牌结果 —— 用手工构造的确定状态（见 test_uno.py 的
    `_setup`），项目有过「测试靠随机性变 flaky」的教训。
    """
    n = len(user_ids)
    if n < 2 or n > 4:
        raise ValueError("UNO 需要 2-4 名玩家")
    resolved = uno_rules.normalize(rules)
    if resolved["teams"] == TEAMS_2V2 and n != TEAM_PLAYERS:
        raise ValueError("组队模式需要恰好 4 名玩家")
    deck = build_deck(resolved["deck"], teams=resolved["teams"] == TEAMS_2V2)
    random.shuffle(deck)
    hands = {seat: [deck.pop() for _ in range(HAND_SIZE)] for seat in range(n)}

    # 起始牌取**第一张数字牌**：官方规则是「若为 +4 则放回重洗」，取数字牌更简单
    # 且顺带消掉「起始就是功能牌，那该谁先动」那一圈边界情况。
    idx = next(i for i, c in enumerate(deck) if card_kind(c).isdigit())
    first = deck.pop(idx)

    return {
        "num_players": n,
        "seat_uids": list(user_ids),
        # 房规（`uno_rules` 的唯一来源）。落进 gs 才能跟着对局一起热重启 ——
        # 它是 str 键 dict，`persistence.py` 的编码器天然能过。
        "rules": resolved,
        "deck_size": len(deck) + HAND_SIZE * n + 1,   # 这副牌一共多少张（战绩与调试用）
        "hands": hands,                 # seat -> [牌]（私有）
        "draw_pile": deck,              # 抽牌堆（pop 取顶）
        "discard_pile": [first],        # 弃牌堆，末位 = 当前牌
        "current_color": card_color(first),
        "turn": 0,
        "direction": 1,                 # 1 顺时针 / -1 逆时针（反转牌改）
        "pending_draw": 0,              # 待罚牌数（叠加房规的核心）
        "pending_kind": None,
        "drawn_this_turn": None,        # 本回合刚摸到的牌（摸到能出则允许立即出）
        # 颜色轮盘（`W+RO`）：`{"seat": 该点名的座位}` 或 None。
        # **非 None 时全场只有那个座位能动**，而且只能发 `uno_roulette`。
        "roulette": None,
        "uno_called": {},               # seat -> True
        "out": [],                      # 已出局的 seat（认输 / 断线判负）
        # 组队档：**已经出完手牌**的 seat（他们同时也在 `out` 里 —— 不再轮到他们，
        # 但队伍还在打）。与 `out` 分开记，是因为「出完了」和「认输了」在组队档里
        # 是两件完全不同的事：前者算队伍的战果，后者是减员。
        "emptied": [],
        "winner": None,                 # seat
        # 组队档的胜负判据（`winner` 在组队档里只是该队的一个代表座位）
        "winner_team": None,
        "reason": None,
        "finished": False,
        "turn_count": 0,
        # ws.py 里多处通用循环把 player_colors 当「玩家 uid 集合」用（`for pid in
        # gs["player_colors"]`），**留空会让所有广播静默地一个都不发**。
        "player_colors": {uid: PLAYER_COLORS[i] for i, uid in enumerate(user_ids)},
    }


def seat_of(game: dict, user_id: int) -> int | None:
    """user_id → seat；不在局中返回 None。"""
    try:
        return game["seat_uids"].index(user_id)
    except ValueError:
        return None


def uid_of(game: dict, seat: int) -> int | None:
    if 0 <= seat < len(game["seat_uids"]):
        return game["seat_uids"][seat]
    return None


def active_seats(game: dict) -> list[int]:
    """**还在轮转**的座位（`out` 之外的人）。

    组队档里「出完手牌」的座位也会进 `out`（见 `_finish_emptied`）—— 他还在队伍里，
    但回合不再轮到他。所以这一个谓词同时回答了「下一个轮到谁」「换手能换给谁」
    「谁还能喊 UNO」三件事，不需要再分一份「出完但没出局」的名单。
    """
    return [s for s in range(game["num_players"]) if s not in game["out"]]


def team_of(game: dict, seat: int) -> int | None:
    """座位属于哪一队；没开组队档时返回 None。

    **对家是队友**：`seat % 2`。四个座位围一圈，0/2 一队、1/3 一队 —— 这样两队
    总是交替行动，牌堆顶该轮到谁也不会因为队伍关系而偏向某一队。
    """
    if game["rules"]["teams"] != TEAMS_2V2:
        return None
    return seat % 2


def team_seats(game: dict, team: int) -> list[int]:
    """某一队的两名成员（升序）。"""
    return [s for s in range(game["num_players"]) if team_of(game, s) == team]


def team_of_uid(game: dict, user_id: int) -> int | None:
    seat = seat_of(game, user_id)
    return None if seat is None else team_of(game, seat)


def turn_uid(game: dict) -> int | None:
    return uid_of(game, game["turn"])


# ── 可出判定（唯一实现） ─────────────────────────────────────


def _matches_color_or_kind(game: dict, card: str) -> bool:
    """按「当前有效颜色 / 当前牌面」判断这张牌能不能出（不管 pending）。

    黑牌恒可出（简化，见模块 docstring）。比牌面时拿**弃牌堆顶**那张比，不是拿
    current_color —— 变色牌之后 current_color 已经被出牌者改过，牌面的数字不再
    是判断依据了（标准规则：变色牌之后只能跟新颜色）。
    """
    if is_wild(card):
        return True
    if card_color(card) == game["current_color"]:
        return True
    kind = card_kind(card)
    if not kind:
        return False
    top = game["discard_pile"][-1] if game["discard_pile"] else None
    return bool(top) and kind == card_kind(top)



def can_answer_penalty(game: dict, card: str) -> bool:
    """被罚牌时这张牌能不能**转嫁**出去（叠加房规的唯一实现）。

    三种口径：
    - `off`：谁都不能转嫁，被罚的人只能摸牌。
    - `same`：同型才能叠（+2 叠 +2、+4 叠 +4）—— 本作 v1.9 以来的默认。
    - `upgrade`：**等值或更高**都能叠（+2 可以叠 +2 / +4 / +6 / +10）。
      注意它**只看罚牌面值，不看这张牌本身合不合法**（No Mercy 的官方口径）——
      否则会出现「被 +4 罚、手里有 +4 却出不了」这种自相矛盾的局面。
    """
    mode = game["rules"]["stack_mode"]
    if mode == "off":
        return False
    kind = card_kind(card)
    need = game["pending_kind"]          # **就是牌面本身**（`"+2"` / `"+4"` / …）
    if mode == "same":
        return kind == need
    if kind not in PENALTY_VALUE:
        return False
    return PENALTY_VALUE[kind] >= PENALTY_VALUE[need]


def can_play_now(game: dict, card: str) -> bool:
    """这张牌**此刻**能不能出（含叠加约束）。"""
    if game["pending_draw"] > 0:
        return can_answer_penalty(game, card)
    return _matches_color_or_kind(game, card)


def playable_cards(game: dict, seat: int | None) -> list[str]:
    """该座位手里所有可出的牌（下发给客户端做高亮）。

    返回牌码列表。手牌里有两张一模一样的牌时两个位置都可出 —— 牌码相同，客户端
    按牌码判断即可（出牌也只传牌码，去掉一张）。
    """
    if seat is None or game["finished"] or seat in game["out"]:
        return []
    if game["turn"] != seat or game.get("roulette") is not None:
        return []
    hand = game["hands"].get(seat, [])
    # 摸过牌之后只能出**刚摸的那张**（标准规则：摸牌后若可出则可出，否则回合结束）
    if game["drawn_this_turn"] is not None:
        card = game["drawn_this_turn"]
        return [card] if can_play_now(game, card) else []
    return [c for c in hand if can_play_now(game, c)]


def jumpable_cards(game: dict, seat: int | None) -> list[str]:
    """该座位此刻能**抢出**的牌（`jump_in` 房规）。

    只有一条判据：与弃牌堆顶**完全相同**（同色同面）。

    两处刻意的收窄，都写进 `docs/游戏设计/UNO/设计.md`：

    - **黑牌不能抢出**。黑牌没有颜色身份（Mattel 的 JUMP-IN 规则里那条「第二张
      相同的 +4 抵消第一张」是另一套机制，本作不做），放开它会让「抢出的 +4 到底
      罚几张」变成一个没人能一眼答上来的问题。
    - **轮到我时不列**。这时候走正常出牌就行，列出来只会让同一张牌在界面上既算
      「可出」又算「可抢」，白添一层歧义。

    「抢出」是本作**唯一一条非本回合动作**，所以 `playable_cards` 与它是两个函数：
    前者回答「轮到我了能出什么」，后者回答「不轮到我时我能抢什么」。客户端的
    高亮读前者、抢出徽标读后者 —— 两者不能合并，否则「轮到我」这件事就丢了。
    """
    rules = game["rules"]
    if not rules["jump_in"] or seat is None or game["finished"] or seat in game["out"]:
        return []
    if game["turn"] == seat:
        return []
    top = game["discard_pile"][-1] if game["discard_pile"] else None
    if not top or is_wild(top):
        return []
    return [c for c in game["hands"].get(seat, []) if c == top]


def can_jump(game: dict, seat: int | None, card: str) -> bool:
    """这张牌此刻能不能抢出。`play` 与 `game_to_sync` 都走这里（分两处写必然漂移）。"""
    return card in jumpable_cards(game, seat)


def roulette_seat(game: dict) -> int | None:
    """正在等谁点名（没有轮盘挂起时 None）。"""
    r = game.get("roulette")
    return r["seat"] if r else None


def _mate_seat(game: dict, seat: int | None) -> int | None:
    """组队档里这个座位的队友；非组队档返回 ``None``。"""
    if seat is None or game["rules"]["teams"] != TEAMS_2V2:
        return None
    team = team_of(game, seat)
    if team is None:
        return None
    for s in team_seats(game, team):
        if s != seat:
            return s
    return None


def uno_call_targets(game: dict, seat: int | None) -> list[int]:
    """这个座位此刻**可以替谁**喊 UNO：先是自己，再是队友（组队档）。

    「队友可以替你喊 UNO」是官方 UNO TEAMS 明写的一条（Mattel JLG93 说明书
    CALLING "UNO!" 一节：「Your partner may also yell "UNO!" for you.」），
    而且是**唯一**能救下「只剩一张却没喊、被对家抓到要摸 2 张」的队友动作。
    """
    if seat is None or game["finished"] or seat in game["out"]:
        return []
    targets: list[int] = []
    if len(game["hands"].get(seat, [])) == 1 and not game["uno_called"].get(seat):
        targets.append(seat)
    mate = _mate_seat(game, seat)
    if (mate is not None and len(game["hands"].get(mate, [])) == 1
            and not game["uno_called"].get(mate)):
        targets.append(mate)
    return targets


def can_call_uno(game: dict, seat: int | None) -> bool:
    """该座位此刻能不能喊 UNO（自己有、或者队友有且没喊过）。

    `call_uno` 的校验与 `game_to_sync` 下发的 `can_call_uno` 都走这里 —— 分两处写
    必然漂移（客户端亮着按钮、服务端却拒收）。
    """
    return bool(uno_call_targets(game, seat))


# ── 动作 ────────────────────────────────────────────────────


def play(game: dict, seat: int, card: str, chosen_color: str | None = None,
         jump: bool = False, target: int | None = None) -> dict:
    """出牌。返回 {ok, card, seat, effect, skipped, target, winner, game_over}。

    - `jump=True` 是**抢出**（`jump_in` 房规）：非本回合、与弃牌堆顶完全相同的牌。
      抢出成功后**回合从抢出者继续**（Mattel 的 JUMP-IN 口径），牌面效果照常结算。
    - `target` 只有 7-换手 需要（换给哪个座位）。**必须在出牌之前校验** ——
      先出牌再报「没给换手对象」的话，牌已经落地了，状态就回不去了。
    """
    err = _guard_play(game, seat, card, chosen_color, jump, target)
    if err:
        return {"ok": False, "error": err}

    hand = game["hands"][seat]
    hand.remove(card)
    game["discard_pile"].append(card)
    game["drawn_this_turn"] = None
    game["last_takeover"] = None      # 每手清一次，只报这一手发生的事
    game["current_color"] = chosen_color if is_wild(card) else card_color(card)

    if jump:
        # **回合从抢出者继续**。顺手把所有座位的「本回合摸过的牌」作废 ——
        # 抢出可能发生在「某人刚摸完牌、还没决定出不出」的那一瞬间，而
        # `drawn_this_turn` 是全局唯一的那个字段：不清的话它仍指着那张早已
        # 不在他手里的牌，`playable_cards` 会一直回空表，**那个人从此出不了
        # 任何牌**（真坑，回归见 TestJumpIn::test_jump_in_clears_a_pending_draw）。
        game["turn"] = seat

    if not hand and not _finish_emptied(game, seat):
        # 出完手牌且**真的打完了** → 经典档直接终局（牌面效果与回合移交都不再结算，
        # 局已经结束了，再推进回合只会让 game_over 时的 turn 是个没意义的值）；
        # 组队档「队友只剩一张」时本人也留在场外，`_settle_turn` 把回合挪开。
        # 反过来，组队档接管了队友一半手牌时 `_finish_emptied` 返回 True，
        # 这一手**照常往下走**（牌面效果 + 移交回合），因为他接着打。
        _settle_turn(game)
        return _play_result(game, seat, card, "empty", [], jump=jump)

    effect, skipped, swapped = _apply_effect(game, seat, card, target)
    if not game["hands"].get(seat) and not game["finished"]:
        # 「弃同色」可能一次把整手牌清空 —— 那也是出完手牌
        _finish_emptied(game, seat)
        _settle_turn(game)
        return _play_result(game, seat, card, "empty", skipped,
                            target=swapped, jump=jump)
    return _play_result(game, seat, card, effect, skipped, target=swapped, jump=jump)


def _guard_play(game: dict, seat: int, card: str, chosen_color: str | None,
                jump: bool, target: int | None) -> str | None:
    """出牌的前置校验（含抢出与 7 换手），返回错误文案或 None。

    **次序有讲究**：`target` 的校验一定要在「把牌丢进弃牌堆」之前 ——
    先出牌再报「没给换手对象」的话，牌已经落地了，状态就回不去了。
    """
    err = _guard_jump(game, seat, card) if jump else _guard_turn(game, seat)
    if err:
        return err
    if not jump and card not in playable_cards(game, seat):
        return "这张牌现在不能出"
    if card_kind(card) in WILD_NEEDS_COLOR and chosen_color not in COLORS:
        return "变色牌需要指定颜色"
    if _is_swap_play(game, card) and not _can_swap_with(game, seat, target):
        return "换手要选一名还在牌局里的对手"
    return None


def _is_swap_play(game: dict, card: str) -> bool:
    """这一手是不是「7 换手」（要选人）。校验与效果结算共用同一条判据。"""
    return (card_kind(card) == "7"
            and game["rules"]["seven_zero"] in ("swap", "both"))


def _apply_effect(game: dict, seat: int, card: str,
                  target: int | None) -> tuple[str, list[int], int | None]:
    """结算牌面效果并移交回合。返回 `(effect, 被跳过的座位, 换手对象)`。

    牌已经在弃牌堆上了（调用方负责），这里只管「这张牌做了什么」。

    **7 / 0 的分支排在数字牌的兜底之前** —— 它们也是数字牌，靠 `kind` 分辨
    （`SKIP` 是 `"S"`、`REVERSE` 是 `"R"`、`DRAW2` 是 `"+2"`，都不与 `"0"`..`"9"` 撞）。
    """
    kind = card_kind(card)
    skipped: list[int] = []
    if kind == SKIP:
        return "skip", _skip(game, 2), None
    if kind == SKIP_ALL:
        # 「跳过所有人」：其他在局玩家全部被跳过、**回合回到自己**。走 `_skip` 绕一圈
        # （步数 = 在局人数）而不是直接 `turn = seat` —— 沿途经过的那些人才会被记进
        # 「被跳过的座位」，界面上那句「跳过 XX、YY」才有得说。
        return "skip_all", _skip(game, len(active_seats(game))), None
    if kind == REVERSE:
        # 2 人局的反转 = 跳过（官方规则）。用**在局人数**判断，这样 3-4 人局里
        # 别人都出局之后剩下的两个人也走同一条分支。
        if len(active_seats(game)) <= 2:
            return "skip", _skip(game, 2), None
        game["direction"] *= -1
        _advance(game, 1)
        return "reverse", skipped, None
    if kind in PENALTY_VALUE:
        if kind == REV_DRAW4:
            # 变色反转 +4：**先转向**，然后那个方向的下家摸 4 并停一轮
            game["direction"] *= -1
        game["pending_draw"] += PENALTY_VALUE[kind]
        game["pending_kind"] = kind
        _advance(game, 1)
        return _EFFECT_OF_PENALTY[kind], skipped, None
    if kind == ROULETTE:
        # 颜色轮盘：**不罚固定的张数**，而是把「点名」这件事交给下家 ——
        # 他点一个颜色，然后一直翻牌到出现该色为止，翻出来的全归他。
        # 所以这里只把状态挂上、把回合交给他，等他发 `uno_roulette` 再结算。
        _advance(game, 1)
        game["roulette"] = {"seat": game["turn"]}
        return "roulette", skipped, None
    if kind == TEAM_CARD_KINDS[0]:
        return _apply_team_play(game, seat)
    if kind == TEAM_CARD_KINDS[1]:
        return _apply_team_draw4(game, seat)
    if kind == DISCARD_ALL or _is_swap_play(game, card) or _is_rotate_play(game, card):
        return _apply_hand_effect(game, seat, card, target)
    _advance(game, 1)
    return "play", skipped, None


def _apply_team_play(game: dict, seat: int) -> tuple[str, list[int], int | None]:
    """Wild Team Play：**回合直接跳到队友**（官方 UNO TEAMS 的第一张专属黑牌）。

    原文：「Play then SKIPS directly to your partner. If they can play a card on
    their turn, they do and play continues from there. **If they CAN'T play a card,
    you must draw 2 cards as a penalty.** Play continues from your partner with the
    chosen color.」

    两条容易做反的地方：
    - 「出不了」在**落地那一刻**判（牌面刚变、还没人动过），罚的是**出牌者自己**
      而不是队友 —— 这张牌的定位是「替队友抢一个回合」，队友接不住就自己认赔。
    - 队友在**场外**时先按同一判据走：他确实出不了，我摸 2 —— 而这一摸会触发
      TAGGING BACK IN 把他拉回来（`_give` → `_tag_back_in`），于是回合交给他正合适。
    """
    mate = _mate_seat(game, seat)
    if mate is None:
        _advance(game, 1)                     # 不该发生：这张牌只在组队档进牌堆
        return "team_play", [], None
    game["turn"] = mate
    if not playable_cards(game, mate):
        # 队友一张都出不了 → 罚**出牌者**摸 2。这一摸会顺手触发 TAGGING BACK IN
        # （队友在场外的话），所以摸完再把回合交给他正合适。
        _give(game, seat, _take(game, 2))
        game["turn"] = mate
    skipped = [s for s in active_seats(game) if s not in (seat, mate)]
    if game["turn"] in game["out"]:
        # 队友既出不了、又没被拉回来（抽牌堆空了之类）—— 回合不能停在出局者身上
        _advance(game, 1)
    return "team_play", skipped, None


def _apply_team_draw4(game: dict, seat: int) -> tuple[str, list[int], int | None]:
    """Wild Team Draw Four：下家摸 2 并停一轮，**他的队友也摸 2**。

    原文：「the next player must draw 2 cards and skip their turn, and their partner
    must also draw 2 cards (**even if they are currently out of the game**).」
    括号那句是关键：场外的队友照样摸 —— 摸完按 TAGGING BACK IN 就回来了。
    所以这是一张「一次打掉对手**一整队** 4 张」的牌，也是组队档里最重的一手。
    """
    order = active_seats(game)
    if seat not in order or len(order) < 2:
        _advance(game, 1)
        return "team_draw4", [], None
    victim = order[(order.index(seat) + game["direction"]) % len(order)]
    _give(game, victim, _take(game, 2))
    mate = _mate_seat(game, victim)
    if mate is not None:
        _give(game, mate, _take(game, 2))
    game["turn"] = victim
    _advance(game, 1)                         # 受害者停一轮
    return "team_draw4", [victim], None


def _is_rotate_play(game: dict, card: str) -> bool:
    """这一手是不是「0 传手」。"""
    return (card_kind(card) == "0"
            and game["rules"]["seven_zero"] in ("rotate", "both"))


def _apply_hand_effect(game: dict, seat: int, card: str,
                       target: int | None) -> tuple[str, list[int], int | None]:
    """**整手牌会动**的那三种：弃同色 / 7 换手 / 0 传手。

    单独一处的原因：它们都会改别人（或自己）的整整一手牌，所以除了结算效果，
    还都得 `_refresh_uno_flags`（谁还剩一张要重新喊）与 `_apply_mercy_to_all`
    （接到一手大牌的人可能直接被 25 张淘汰）。把它们和「只是推一下回合」的
    数字牌混在一个 if 链里，这两条收尾很容易漏掉其中一处。
    """
    kind = card_kind(card)
    if kind == DISCARD_ALL:
        # 「弃同色」：把手里其余同色牌一并丢进弃牌堆（它们**不结算效果**，
        # 官方口径就是「当普通牌丢掉」）。可能一次清空整手 —— 那是「出完手牌」，
        # 由 `play` 在那之后再查一次。
        _discard_all_of_color(game, seat, card_color(card))
        _refresh_uno_flags(game)
        _advance(game, 1)
        return "discard_all", [], None
    if _is_swap_play(game, card):
        _swap_hands(game, seat, target)
        effect, target_out = "swap", target
    else:
        # 0 传手：所有人按当前方向把手牌传给下家（只在**在局**座位之间传，
        # 出局者的手牌只用于排名，不参与流转）。
        _rotate_hands(game)
        effect, target_out = "rotate", None
    _refresh_uno_flags(game)
    _apply_mercy_to_all(game)
    _advance(game, 1)
    return effect, [], target_out


def draw(game: dict, seat: int, auto: bool = False) -> dict:
    """摸牌。返回 {ok, drawn, penalty, kept_turn, game_over}。

    - 有待罚牌（`pending_draw > 0`）→ 一次摸完累计张数、清空罚牌、**回合移交**
      （被罚的人这一轮就结束了，不能再出牌）。
    - 否则摸 1 张：摸到能出的牌**保留回合**让本人决定出还是过（`kept_turn=True`，
      同时记进 `drawn_this_turn`）；不能出就自动移交。
    - `draw_until_play` 房规下**一直摸到能出为止**（摸穿牌堆就停），手牌会涨得很快。
    - `must_play` 房规下**手里有能出的牌就不许摸**（`auto=True` 的断线代打不受此限，
      否则代打会卡在一条被拒的摸牌上、回合永远交不出去）。
    - `auto=True` 是断线玩家的自动过牌：摸完一律移交，不保留回合。
    """
    err = _guard_turn(game, seat)
    if err:
        return {"ok": False, "error": err}
    if game["drawn_this_turn"] is not None:
        return {"ok": False, "error": "本回合已经摸过牌了"}
    game["last_takeover"] = None      # 每手清一次，只报这一手发生的事
    if not auto and game["rules"]["must_play"] and game["pending_draw"] == 0 \
            and playable_cards(game, seat):
        return {"ok": False, "error": "手里有能出的牌，必须先出"}

    if game["pending_draw"] > 0:
        return _take_penalty(game, seat)

    got = _draw_for_play(game)
    if not got:
        # 抽牌堆与弃牌堆都空了（极端边界）：直接过回合
        _advance(game, 1)
        return {"ok": True, "drawn": [], "penalty": 0, "kept_turn": False,
                "takeover": game.get("last_takeover"),
                "game_over": _game_over_payload(game)}

    card = got[-1]
    _give(game, seat, got)
    # **`game_over` 必须逐条路径都带上 `_game_over_payload`**：`_give` 里会判
    # 「25 张淘汰」，摸牌把一个人顶出局、场上只剩一个时这局就结束了 ——
    # 恒返回 None 的话客户端永远收不到 `game_over`，界面上停在旧盘面不声不响。
    if seat in game["out"]:
        # 刚被这手牌顶过 25 张：**回合不能留在一个已经出局的座位上**。
        # 这条在「摸到能出为止」档下必然触发 —— 那时最后一张一定可出，
        # 于是走到下面的「保留回合」分支、把一个出局的人当成当前回合玩家。
        # 由引擎自对弈模糊测试抓到（`回合落在已出局的座位上`）。
        _advance(game, 1)
        return {"ok": True, "drawn": got, "penalty": 0, "kept_turn": False,
                "takeover": game.get("last_takeover"),
                "game_over": _game_over_payload(game)}
    if can_play_now(game, card) and not auto:
        # `drawn_this_turn` 记**最后摸到的那张** —— 摸到能出为止时它就是那张
        # 「摸出来的能打的牌」，与 `playable_cards` 的口径一致。
        game["drawn_this_turn"] = card
        return {"ok": True, "drawn": got, "penalty": 0, "kept_turn": True,
                "takeover": game.get("last_takeover"),
                "game_over": _game_over_payload(game)}
    _advance(game, 1)
    return {"ok": True, "drawn": got, "penalty": 0, "kept_turn": False,
            "takeover": game.get("last_takeover"),
            "game_over": _game_over_payload(game)}


def pass_turn(game: dict, seat: int) -> dict:
    """摸到牌之后选择不出，移交回合。"""
    err = _guard_turn(game, seat)
    if err:
        return {"ok": False, "error": err}
    if game["drawn_this_turn"] is None:
        return {"ok": False, "error": "现在没有可以放弃的出牌"}
    if game["rules"]["must_play"]:
        return {"ok": False, "error": "手里有能出的牌，必须先出"}
    game["drawn_this_turn"] = None
    _advance(game, 1)
    return {"ok": True, "game_over": _game_over_payload(game)}


def auto_pass(game: dict, seat: int) -> dict:
    """断线玩家轮到他时由服务端代打：把回合交出去。

    为什么不一味等 120s 保座：UNO 是回合制，一个掉线的人能让整桌停 2 分钟。
    自动过牌是 UNO 里代价最小的动作（只是摸一张）。**与 120s 判负是两件事** ——
    这个只是让牌局继续，该玩家的座位与身份都保留；超时判负仍由
    `room_manager._on_dc_timeout` 负责。

    调用方必须先确认「除断线者外还有在线的活跃玩家」，否则回合会在几个断线座位
    之间无限自转（见 `ws._maybe_autopass_uno`）。
    """
    if game["turn"] != seat or game["finished"] or seat in game["out"]:
        return {"ok": False, "error": "现在不该他动"}
    if game["drawn_this_turn"] is not None:
        game["drawn_this_turn"] = None
        _advance(game, 1)
        return {"ok": True, "auto": "pass",
                "takeover": game.get("last_takeover"),
                "game_over": _game_over_payload(game)}
    # `must_play` 档下他连摸牌都会被引擎拒（那条档位就是「有牌就不许摸」），
    # 代打必须替他打一张，否则回合永远交不出去。**同深空休眠「人机每个动作都要
    # 先过引擎校验，否则一次决策一个动作会死循环」那条教训。**
    if game["rules"]["must_play"] and game["pending_draw"] == 0:
        options = playable_cards(game, seat)
        if options:
            card = options[0]
            return play(game, seat, card, best_color_for(game, seat, card))
    return draw(game, seat, auto=True)


def call_uno(game: dict, seat: int, target: int | None = None) -> dict:
    """喊 UNO。本版**不抓罚**，只是置标记 + 广播。

    `target` 省略时：自己有牌可喊就喊自己，否则替队友喊（见 `uno_call_targets`）。
    两边都只剩一张时会优先喊自己 —— 队友那一张他自己也能喊。
    """
    targets = uno_call_targets(game, seat)
    if not targets:
        return {"ok": False, "error": "现在不能喊 UNO"}
    # `targets` 的顺序就是「先自己、再队友」——不指定时取第一个。
    # ⚠️ 不能写成 `who = seat`：自己两张、队友一张时 `seat` 不在 `targets` 里，
    # 那条默认路径会把「替队友喊」变成一句拒绝（点了没反应）。
    who = targets[0] if target is None else int(target)
    if who not in targets:
        return {"ok": False, "error": "不能替他喊 UNO"}
    game["uno_called"][who] = True
    return {"ok": True, "seat": who, "for_mate": who != seat}


def surrender(game: dict, seat: int) -> dict | None:
    """认输：**只让本人出局，牌局继续**（与扫雷/飞行棋/2048 同类）。

    只剩一个人还在局中时才终局。返回 game_over 载荷，或 None 表示继续。
    """
    if game["finished"] or seat in game["out"]:
        return None
    game["out"].append(seat)
    game["uno_called"].pop(seat, None)
    if game["turn"] == seat:
        game["drawn_this_turn"] = None
        _advance(game, 1)
    return _check_game_over(game)


# ── 结算 ────────────────────────────────────────────────────


def compute_ranking(game: dict, players_info: list[dict]) -> list[dict]:
    """终局排名。

    排序键（经典/无怜悯）：**胜者最前 → 剩余手牌点数升序 → 同分时认输的靠后 →
    座位号**。单局定胜负下只有一个人出完手牌，其余人的名次由剩余点数决定
    （点数少 = 更接近赢，符合直觉）；认输的人保留认输当时的手牌点数参与排序，
    但同分时排后面。

    **组队档的排序键不一样**：先按队伍（获胜那队全员在前），队内再按点数。
    这时「胜者」是一个人而是一队 —— 拿单个 `winner` 座位去排，队友会被排到
    输队后面去（战绩页看起来就像「他一个人赢的」）。

    每一条都带 `team`（没开组队时 None）与 `emptied`（这人是不是出完手牌才结束
    的），战绩页据此说「蓝队获胜」而不是「某某获胜」。
    """
    winner = game.get("winner")
    win_team = game.get("winner_team")
    team_mode = game["rules"]["teams"] == TEAMS_2V2
    entries = []
    for info in players_info:
        seat = info["seat"]
        hand = game["hands"].get(seat, [])
        entries.append({
            "seat": seat,
            "username": info["username"],
            "nickname": info.get("nickname"),
            "cards": len(hand),
            "points": hand_points(hand),
            "surrendered": seat in game["out"] and seat not in game["emptied"],
            "team": team_of(game, seat),
            "emptied": seat in game["emptied"],
        })

    def head(e: dict) -> int:
        """第一排序键：0 = 靠前的那一档。"""
        if team_mode:
            return 0 if e["team"] == win_team else 1
        return 0 if e["seat"] == winner else 1

    entries.sort(key=lambda e: (
        head(e),
        e["points"],
        1 if e["surrendered"] else 0,
        e["seat"],
    ))

    result = []
    for i, e in enumerate(entries):
        rank = i + 1
        if i > 0:
            prev = entries[i - 1]
            if head(e) == head(prev) and e["points"] == prev["points"] \
                    and e["surrendered"] == prev["surrendered"]:
                rank = result[-1]["rank"]   # 完全并列，共用名次
        result.append({**e, "rank": rank})
    return result


def snapshot(game: dict) -> dict:
    """战绩快照：保住终局手牌与点数（战绩页要能回放「谁手里剩了什么」）。

    带上 `rules`：战绩页要能说清「这局打的是哪套规矩」—— 同一个 UNO 在「热闹」档
    和「经典」档下完全是两种牌局，只写一个游戏名等于什么都没说。
    """
    return {
        "game": "uno",
        "num_players": game["num_players"],
        "seat_uids": list(game["seat_uids"]),
        "rules": dict(game["rules"]),
        # 档位名**在服务端就翻译好**：战绩页只说一句「玩法：热闹」，它不该为了这句话
        # 去抄一份档位表（同「游戏名散在七处」那条坑）。
        "rules_name": uno_rules.preset_name(game["rules"]),
        "hands": {str(s): list(h) for s, h in game["hands"].items()},
        "points": {str(s): hand_points(h) for s, h in game["hands"].items()},
        "discard_top": game["discard_pile"][-1] if game["discard_pile"] else None,
        "winner": game.get("winner"),
        # 组队档的胜负是**队伍**，战绩页要说「蓝队获胜」而不是「某某获胜」
        "winner_team": game.get("winner_team"),
        "seat_teams": seat_teams(game),
        "emptied": list(game.get("emptied") or []),
        "reason": game.get("reason"),
    }


def seat_teams(game: dict) -> list[int | None]:
    """座位 → 队伍（没开组队档时全是 None）。随状态下发，客户端不自己算 `% 2`。"""
    return [team_of(game, s) for s in range(game["num_players"])]


def game_to_sync(game: dict, user_id: int) -> dict:
    """逐人下发：**只给自己的手牌**，别人的只给张数。

    `player_colors`（uid→玩家身份色）不在这里下发 —— 它由 ws 层按房间名单一并
    给出，与其它游戏保持一致的形状。
    """
    seat = seat_of(game, user_id)
    hand = list(game["hands"].get(seat, [])) if seat is not None else []
    return {
        "game": "uno",
        "num_players": game["num_players"],
        "seat_uids": list(game["seat_uids"]),
        # 座位 → 玩家身份色（顶层 theme.playerColors 的键）。跟着状态一起下发，
        # 省得客户端再维护一份「第几个座位是什么颜色」的映射。
        "seat_colors": [
            game["player_colors"].get(uid_of(game, s), "")
            for s in range(game["num_players"])
        ],
        "your_seat": seat,
        # 房规跟着状态下发：客户端**不用它判规则**（规则全在服务端），只用它把
        # 提示文案说对（「抢出」「7 换手」这些词得知道本局开没开）。
        "rules": dict(game["rules"]),
        "hand": hand,
        "playable": playable_cards(game, seat),
        # 能**抢出**的牌（`jump_in` 房规）。与 `playable` 是两个不同的东西：
        # `playable` 回答「轮到我了能出什么」，`jumpable` 回答「不轮到我时我能抢
        # 什么」。客户端给它们的视觉通道也必须分开（见 UnoScreen 的「抢出」徽标）。
        "jumpable": jumpable_cards(game, seat),
        "hand_counts": {str(s): len(h) for s, h in game["hands"].items()},
        "discard_top": game["discard_pile"][-1] if game["discard_pile"] else None,
        "current_color": game["current_color"],
        "turn": game["turn"],
        "direction": game["direction"],
        "pending_draw": game["pending_draw"],
        "pending_kind": game["pending_kind"],
        "uno_called": sorted(game["uno_called"]),
        "out": list(game["out"]),
        # 组队档（`teams=2v2`）：座位 → 队伍、哪些座位已经出完手牌、赢的是哪一队。
        # **客户端不自己算 `seat % 2`** —— 对家配对是规则，规则只在服务端
        # （同「可出判定不由客户端算」）。
        "seat_teams": seat_teams(game),
        "emptied": list(game.get("emptied") or []),
        "your_team": team_of(game, seat) if seat is not None else None,
        "winner_team": game.get("winner_team"),
        "draw_pile_count": len(game["draw_pile"]),
        **_action_flags(game, seat),
        "winner": game.get("winner"),
        "reason": game.get("reason"),
        "finished": game["finished"],
    }


# ── 内部工具 ────────────────────────────────────────────────


def best_color(hand: list[str]) -> str:
    """黑牌选色：选手里最多的颜色；同数量时按 `COLORS` 顺序取（保证确定性）。

    一手全是黑牌（理论上可能）时返回 `COLORS[0]` —— 没有更好的依据。
    放在引擎里而不是 AI 里：断线代打（`auto_pass`）也要选色，而 AI 是上层
    （`uno_ai` 反过来 import 引擎），引擎不能反过来依赖它。
    """
    counts = Counter(card_color(c) for c in hand if not is_wild(c))
    if not counts:
        return COLORS[0]
    return max(COLORS, key=lambda col: (counts.get(col, 0), -COLORS.index(col)))


def best_color_for(game: dict, seat: int, card: str) -> str | None:
    """出 `card` 时要报的颜色：黑牌报手里最多的那个色，其余牌返回 None。

    **手牌要先去掉这张再算** —— 出的那张已经不在手里了。
    """
    if not is_wild(card):
        return None
    rest = list(game["hands"].get(seat, []))
    rest.remove(card)
    return best_color(rest)


def _guard_turn(game: dict, seat: int | None) -> str | None:
    """动作前置校验，返回错误文案或 None。"""
    if seat is None:
        return "你不在本局中"
    if game["finished"]:
        return "本局已结束"
    if seat in game["out"]:
        return "你已出局"
    if game["turn"] != seat:
        return "还没轮到你"
    if game.get("roulette") is not None:
        # 颜色轮盘挂起时**全场只有一件事能做**：被点名的那个发 `uno_roulette`。
        # 不挡住的话，被点名的人可以靠摸牌/出牌把轮盘绕过去（他的回合还没结束）。
        return "颜色轮盘还没点名，先选一个颜色"
    return None


def resolve_roulette(game: dict, seat: int, color: str) -> dict:
    """颜色轮盘点名：从抽牌堆一直翻到出现 `color` 为止，**翻出来的全归他**并停一轮。

    几个刻意的取舍（都写进 `docs/游戏设计/UNO/设计.md`）：

    - **点名的那张也归他**：官方那句是「翻到出现该色为止，他拿走所有翻出来的牌」，
      字面上包含命中那一张。它不进弃牌堆 —— 进了的话牌桌顶就换成一张谁也没打过的牌。
    - **点名的颜色同时成为当前颜色**（其余黑牌都是这个待遇，轮盘没理由是例外）。
    - **抽牌堆翻穿就停手**，有什么拿什么。
    - 翻出来的牌同样过一遍 25 张淘汰（`_give` 里判）。
    """
    if game.get("roulette") is None or game["roulette"]["seat"] != seat:
        return {"ok": False, "error": "现在不是你在点颜色"}
    if color not in COLORS:
        return {"ok": False, "error": "没有这个颜色"}
    game["roulette"] = None
    game["current_color"] = color
    cards: list[str] = []
    while True:
        got = _take(game, 1)
        if not got:
            break
        cards += got
        if card_color(got[0]) == color:
            break
    _give(game, seat, cards)
    _advance(game, 1)
    return {"ok": True, "seat": seat, "color": color, "drawn": cards,
            "game_over": _game_over_payload(game)}


def _take_penalty(game: dict, seat: int) -> dict:
    """一次摸完累计的罚牌、清空罚牌账，然后移交回合（被罚的人这一轮就结束了）。"""
    n = game["pending_draw"]
    cards = _take(game, n)
    _give(game, seat, cards)
    game["pending_draw"] = 0
    game["pending_kind"] = None
    _advance(game, 1)
    return {"ok": True, "drawn": cards, "penalty": n, "kept_turn": False,
            "takeover": game.get("last_takeover"),
            "game_over": _game_over_payload(game)}


def _draw_for_play(game: dict) -> list[str]:
    """摸牌阶段取到的牌（**还没发给玩家**）。

    经典规则只摸一张；`draw_until_play` 房规下**一直摸到能出为止**。
    终止性靠抽牌堆：`_take` 摸不出牌就返回空表，循环随之结束（摸穿时不会死转）。
    """
    got = _take(game, 1)
    if not got or not game["rules"]["draw_until_play"]:
        return got
    while not can_play_now(game, got[-1]):
        more = _take(game, 1)
        if not more:
            break
        got += more
    return got


def _guard_jump(game: dict, seat: int | None, card: str) -> str | None:
    """抢出的前置校验。**抢输的人必须收到一句能看懂的错**，不能静默 ——
    「被静默拒绝的动作在界面上就是点了没反应」（本项目反复踩过）。"""
    if seat is None:
        return "你不在本局中"
    if game["finished"]:
        return "本局已结束"
    if seat in game["out"]:
        return "你已出局"
    if not game["rules"]["jump_in"]:
        return "本局没有开启抢出"
    if game["turn"] == seat:
        return "轮到你了，正常出牌就行"
    if not can_jump(game, seat, card):
        # 两种可能：这张牌与牌堆顶不同，或者**被别人抢先一步打掉了**。
        # 后一种在抢出房规下很常见（同一张牌可能好几个人都有）。
        return "这张牌抢不了了，可能刚被别人抢走"
    return None


def _action_flags(game: dict, seat: int | None) -> dict:
    """「我现在能做什么」的四个旗标 + 7 换手可以换给谁 + 哪些牌要先选色。

    **客户端一格规则都不重写**，照着这些旗标禁用/启用按钮即可（同象棋的 `legal`）。
    `swap_targets` 空表 = 现在不用选人，客户端据此决定出 7 时弹不弹选人框。
    `color_kinds` 是**出牌前必须先选一个颜色的牌面** —— 它也必须下发，不能让客户端
    自己抄一份：抄漏一张的后果不是渲染错，而是**点了没反应**（客户端会不带颜色把牌
    发上去，服务端以「变色牌需要指定颜色」整手拒收，而玩家没有任何办法补上那个颜色）。
    v1.14 的 `W+6` / `W+10` / `W+RD4` 就是这么漏的 —— 客户端当时只认经典的两张黑牌。
    """
    return {
        "can_draw": _can_draw(game, seat),
        "can_pass": seat is not None and game["turn"] == seat
                    and game["drawn_this_turn"] is not None
                    and not game["rules"]["must_play"],
        "can_call_uno": can_call_uno(game, seat),
        # 能替谁喊 UNO（组队档里可能包含队友）。客户端据此决定按钮文案 ——
        # 「喊 UNO」还是「替队友喊 UNO」。
        "uno_targets": uno_call_targets(game, seat),
        "swap_targets": _swap_targets(game, seat),
        "color_kinds": list(WILD_NEEDS_COLOR),
        # 颜色轮盘：**正等着哪个座位点名**（没有挂起时 None）。与 `turn` 同一个口径 ——
        # 客户端拿 `your_seat` 比一下就知道是不是自己，不必再来一个布尔旗标。
        "roulette_seat": roulette_seat(game),
    }


def _swap_targets(game: dict, seat: int | None) -> list[int]:
    """该座位此刻若打出 7，可以换给哪些座位。

    只在「轮到我 + 7 换手开着 + 我手里有能出的 7」时非空。**空表就是「现在不用
    选人」** —— 客户端据此决定出 7 时弹不弹选人框，不必自己复刻这三条判据
    （同象棋的 `legal`、UNO 自己的 `playable`）。
    """
    if seat is None or game["finished"] or seat in game["out"]:
        return []
    if game["rules"]["seven_zero"] not in ("swap", "both"):
        return []
    if not any(card_kind(c) == "7" for c in playable_cards(game, seat)):
        return []
    return [s for s in active_seats(game) if s != seat]


def _can_draw(game: dict, seat: int | None) -> bool:
    base = (seat is not None and not game["finished"] and seat not in game["out"]
            and game["turn"] == seat and game["drawn_this_turn"] is None
            and game.get("roulette") is None)
    if not base:
        return False
    # `must_play`：手里有能出的牌就不许摸。被罚牌时例外 —— 那时他本来就出不了牌，
    # 摸牌是唯一的出路（`playable_cards` 在有 pending 时只给能转嫁的罚牌）。
    if game["rules"]["must_play"] and game["pending_draw"] == 0 \
            and playable_cards(game, seat):
        return False
    return True


def _play_result(game: dict, seat: int, card: str, effect: str,
                 skipped: list[int], target: int | None = None,
                 jump: bool = False) -> dict:
    return {
        "ok": True,
        "card": card,
        "seat": seat,
        "color": game["current_color"],
        "effect": effect,
        "skipped": skipped,
        # 7 换手的对象（只有 `effect == "swap"` 时有值）
        "target": target,
        # 这一手是不是抢出来的（客户端据此说「XX 抢出了…」而不是「XX 打出了…」）
        "jump": jump,
        # 组队档专属：这一手顺带把队友的一半手牌交了过来（`going_out`），
        # 或者把场外的队友摸了回来（`tag_in`）。没有发生就是 None。
        "takeover": game.get("last_takeover"),
        "pending_draw": game["pending_draw"],
        "winner": game["winner"],
        "game_over": _game_over_payload(game),
    }


def _skip(game: dict, steps: int) -> list[int]:
    """跳过接下来的 steps 个在局座位，返回**真正被跳过**的座位。

    「真正被跳过」= 沿途经过的座位去掉最终轮到的那个。2 人局里 steps=2 时沿途是
    [对手, 我]，最终轮到我 → 被跳过的是 [对手]；若直接返回沿途列表会把我自己也算
    进去（文案就成了「跳过了你」）。
    """
    n = game["num_players"]
    cur = game["turn"]
    walked: list[int] = []
    for _ in range(steps):
        for _ in range(n):
            cur = (cur + game["direction"]) % n
            if cur not in game["out"]:
                walked.append(cur)
                break
    _advance(game, steps)
    return [s for s in dict.fromkeys(walked) if s != game["turn"]]


def _advance(game: dict, steps: int = 1):
    """按当前方向前进 steps 个**在局**座位（出局的座位不占位）。"""
    n = game["num_players"]
    for _ in range(steps):
        moved = False
        for _ in range(n):
            game["turn"] = (game["turn"] + game["direction"]) % n
            if game["turn"] not in game["out"]:
                moved = True
                break
        if not moved:
            return   # 没有在局座位了（终局路径会处理）
    game["turn_count"] += 1


def _take(game: dict, n: int) -> list[str]:
    """从抽牌堆取 n 张（不够就有多少拿多少），中间自动重洗弃牌堆。"""
    out: list[str] = []
    for _ in range(n):
        if not game["draw_pile"] and not _reshuffle(game):
            break
        out.append(game["draw_pile"].pop())
    return out


def _reshuffle(game: dict) -> bool:
    """抽牌堆空了：把弃牌堆除当前牌外洗回来。洗不动（弃牌堆只剩 1 张）返回 False。"""
    if len(game["discard_pile"]) <= 1:
        return False
    top = game["discard_pile"][-1]
    rest = game["discard_pile"][:-1]
    random.shuffle(rest)
    game["draw_pile"] = rest
    game["discard_pile"] = [top]
    return True


def _give(game: dict, seat: int, cards: list[str]):
    """发牌给某座位。手牌回到 2 张以上时清掉 UNO 标记（他不再是「只剩一张」）。"""
    if not cards:
        return
    game["hands"][seat].extend(cards)
    if len(game["hands"][seat]) > 1:
        game["uno_called"].pop(seat, None)
    _apply_mercy(game, seat)
    # 摸到牌就把场外的队友拉回来（TAGGING BACK IN）。被 Mercy 淘汰的人不拉 ——
    # 他已经不在局里了。
    if seat not in game["out"]:
        _tag_back_in(game, seat)


def _discard_all_of_color(game: dict, seat: int, color: str):
    """「弃同色」：把手里**其余**同色牌一并丢进弃牌堆。

    调用时那张「弃同色」本身已经不在手里了（`play` 先 remove 再 append）。
    丢掉的牌**不结算任何效果** —— 官方口径是「当普通牌丢掉」，所以丢一张
    `R+2` 不会让下家摸牌（否则这张牌会变成一次全场乱斗）。
    """
    hand = game["hands"].get(seat, [])
    for c in [c for c in hand if card_color(c) == color]:
        hand.remove(c)
        game["discard_pile"].append(c)


def _apply_mercy(game: dict, seat: int):
    """手牌涨到 25 张就**直接出局**（`mercy` 房规）。

    只在「发牌之后」判（摸牌 / 罚牌 / 换手接过来的一手），**不负责推进回合** ——
    调用它的地方本来就在推进回合，而 `_advance` 会跳过已出局的座位。

    「只剩一个人就终局」复用 `_check_game_over`（认输/判负走的是同一条路）——
    这正是官方说的第二种获胜方式：**不是「出完牌的人被淘汰」，而是「别人都被
    25 张淘汰掉，剩下你一个」**。出完手牌仍然照常获胜。
    """
    limit = MERCY_LIMIT if game["rules"]["mercy"] else None
    if limit is None or game["finished"] or seat in game["out"]:
        return
    if len(game["hands"].get(seat, [])) < limit:
        return
    game["out"].append(seat)
    game["uno_called"].pop(seat, None)
    _check_game_over(game)


def _apply_mercy_to_all(game: dict):
    """换手 / 传手之后逐人判一次（**顺序无关**：成对交换与整环轮转都不影响各自的
    手牌张数，所以不必像 `_claim_rooms` 那样两趟走）。"""
    for s in list(active_seats(game)):
        _apply_mercy(game, s)


# ── 7 换手 / 0 传手（`seven_zero` 房规） ─────────────────────


def _can_swap_with(game: dict, seat: int, target) -> bool:
    """换手对象必须是**另一个还在局中的座位**。出局者不参与 —— 他的牌只用于排名，
    换走会让战绩快照里的「终局手牌」变成一笔糊涂账。"""
    if target is None or isinstance(target, bool) or not isinstance(target, int):
        return False
    if target == seat:
        return False
    return target in active_seats(game)


def _swap_hands(game: dict, a: int, b: int):
    game["hands"][a], game["hands"][b] = game["hands"].get(b, []), game["hands"].get(a, [])


def _rotate_hands(game: dict):
    """所有人按**当前方向**把手牌传给下家（只在在局座位之间传）。

    `order` 是在局座位的升序列表，方向 `d` 下「下家」就是下标 `+d`，所以第 i 位
    收到的正是第 `i-d` 位的手牌。**出局者的手牌不动**（同 `_can_swap_with`）。
    """
    order = active_seats(game)
    if len(order) < 2:
        return
    d = game["direction"]
    hands = game["hands"]
    received = {s: hands.get(order[(i - d) % len(order)], [])
                for i, s in enumerate(order)}
    hands.update(received)


def _refresh_uno_flags(game: dict):
    """换手/传手之后重算 UNO 标记。

    **只清不加**：手里不再是恰好一张的，标记作废；而**接到**一手一张牌的人
    并没有喊过 —— 他得自己再喊一次。换手是双向的，凭「手里一张」白送一个
    UNO 徽标说不过去。
    """
    for s in list(game["uno_called"]):
        if len(game["hands"].get(s, [])) != 1:
            game["uno_called"].pop(s, None)


def _pass_half(game: dict, giver: int, taker: int) -> int:
    """把 `giver` 手牌的**一半（向下取整）**交给 `taker`，返回实际传了几张。

    官方口径（JLG93 GOING OUT 的 NOTE）：奇数张时**传的人留下多的那些** ——
    「if they have 9 cards in their hand, they pass 4 cards to you and keep 5 cards」。
    """
    hand = game["hands"].setdefault(giver, [])
    n = len(hand) // 2
    if n <= 0:
        return 0
    moved = hand[:n]
    del hand[:n]
    game["hands"].setdefault(taker, []).extend(moved)
    # 记一笔给客户端报信：手牌张数会**当场跳变**，不解释一句玩家只会觉得是 bug。
    game["last_takeover"] = {"seat": taker, "from": giver, "count": n, "reason": None}
    return n


def _finish_emptied(game: dict, seat: int) -> bool:
    """某人出完了手牌。返回 **True = 这一手还没完**（组队档接管了队友一半手牌，
    调用方接着按牌面效果正常结算）；False = 局已终局，或本人确实留在了场外。

    - **经典 / 无怜悯**：直接终局，他就是赢家。
    - **组队（`teams=2v2`）**：照官方 UNO TEAMS 的 **GOING OUT** —— 出完手牌
      **不等于出局**，队友把手里一半交过来、本人接着打；**只有队友手里只剩 1 张
      （半手传不出来）时才真的留在场外**，等队友把最后那张打完，一队两人同时
      空手才算赢（`_check_game_over` 的 `REASON_TEAM_EMPTIED`）。
      ⚠️ 这条**推翻了 v1.16 的「出完就进 `out`」**：那一版把「把牌倒干净就跑」
      当成了这一档的张力，而官方说明书里根本不是这样 —— 出完牌是**换一副新牌
      接着打**，张力在于「你队友还剩几张」。
    """
    if game["rules"]["teams"] != TEAMS_2V2:
        game["finished"] = True
        game["reason"] = REASON_EMPTIED
        game["winner"] = seat
        return False
    mate = _mate_seat(game, seat)
    if mate is not None and len(game["hands"].get(mate, [])) >= 2:
        _pass_half(game, mate, seat)
        game["last_takeover"]["reason"] = "going_out"
        _refresh_uno_flags(game)
        return True
    # 队友只剩 1 张（半手传不出来），或者也没牌了 → 本人留在场外等队友出完
    if seat not in game["emptied"]:
        game["emptied"].append(seat)
    if seat not in game["out"]:
        game["out"].append(seat)
    game["uno_called"].pop(seat, None)
    _check_game_over(game)
    return False


def _tag_back_in(game: dict, seat: int):
    """官方 UNO TEAMS 的 **TAGGING BACK IN**：「Even if you're out of the game,
    you can get tagged back in. While you are out, if your partner draws cards for
    any reason they immediately pass you half of their cards and you're back in.」

    所以队友只要**摸到牌**（罚牌 / 摸牌 / 颜色轮盘翻出来的都算），场外的本人
    立刻分到一半、回到轮转里。**认输的队友不拉回来** —— 判据取 `emptied`
    而不是 `out`，认输只进 `out`。
    """
    if game["rules"]["teams"] != TEAMS_2V2:
        return
    mate = _mate_seat(game, seat)
    if mate is None or mate not in game["emptied"]:
        return
    if len(game["hands"].get(seat, [])) < 2:
        return
    _pass_half(game, seat, mate)
    game["last_takeover"]["reason"] = "tag_in"
    game["emptied"].remove(mate)
    if mate in game["out"]:
        game["out"].remove(mate)
    _refresh_uno_flags(game)


def _settle_turn(game: dict):
    """把回合从「已经不再轮到的人」身上挪开。

    组队档专用：出完手牌那条路径**不结算牌面效果、也不推进回合**（经典档里
    出完即终局，推不推都无所谓），于是 `turn` 会停在刚出完牌的那个人身上 ——
    而他在 `out` 里，`playable_cards` 回空表、`uno_should_autopass` 也会判错。
    统一在这里收口，比在 `play` 的每条返回路径上各写一遍稳。
    """
    if game["finished"] or game["turn"] not in game["out"]:
        return
    _advance(game, 1)


def _check_game_over(game: dict) -> dict | None:
    """终局判定。

    - **经典 / 无怜悯**：只剩一个在局玩家就终局（其余都认输/判负了）。
    - **组队**：见下 —— 「一队两人都出完」或「对手整队没了」。
    """
    if game["finished"]:
        return None
    if game["rules"]["teams"] == TEAMS_2V2:
        for team in (0, 1):
            members = team_seats(game, team)
            if members and all(s in game["emptied"] for s in members):
                game["finished"] = True
                game["reason"] = REASON_TEAM_EMPTIED
                game["winner_team"] = team
                # `winner` 是**座位**，全项目都按这个口径读（战绩、排名、广播）。
                # 组队档里取该队第一个出完的那个座位当代表 —— 真正的胜负判据是
                # `winner_team`，客户端结算文案读的是它。
                game["winner"] = next(s for s in members if s in game["emptied"])
                return _game_over_payload(game)
        # 只剩一支队伍还有人能打 → 那队获胜（对手全认输/被 Mercy 淘汰/出完了）
        alive_teams = {team_of(game, s) for s in active_seats(game)}
        if len(alive_teams) <= 1:
            team = next(iter(alive_teams), None)
            game["finished"] = True
            game["reason"] = REASON_LAST_REMAINING
            game["winner_team"] = team
            active = active_seats(game)
            game["winner"] = active[0] if active else None
            return _game_over_payload(game)
        return None
    active = active_seats(game)
    if len(active) <= 1:
        game["finished"] = True
        game["reason"] = REASON_LAST_REMAINING
        game["winner"] = active[0] if active else None
        return _game_over_payload(game)
    return None


def _game_over_payload(game: dict) -> dict | None:
    if not game["finished"]:
        return None
    return {"reason": game["reason"], "winner": game["winner"],
            "winner_team": game.get("winner_team")}