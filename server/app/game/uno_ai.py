"""UNO AI — 贪心策略，无搜索。

接口：
- `best_action(game, seat) -> dict | None`：轮到他时的一个动作
- `best_jump(game, seat) -> dict | None`：**不轮到他时**能不能抢出一张（`jump_in` 房规）

返回**一个动作**，由 `ws.py` 交给引擎执行（引擎仍是唯一权威，AI 只做决策不直接改状态）：

    {"action": "play", "card": "R5", "color": "R"|None, "target": 3|None}
    {"action": "draw"}                                    # 摸牌
    {"action": "pass"}                                    # 摸过牌之后不出
    {"action": "roulette", "color": "R"}                  # 颜色轮盘点名（W+RO）

**与象棋/四子棋不同，这里不需要 `asyncio.to_thread`**：没有搜索、没有时间预算，
一次决策就是几十次比较，微秒级。

策略（贪心，够用就行）：
1. 摸过牌了（`drawn_this_turn`）→ 只能出刚摸的那张或过牌，能出就出（保持节奏）。
2. 有待罚牌 → 手里有能转嫁的罚牌就转给下家，否则摸牌（引擎会一次摸完累计罚牌）。
3. 否则按「数字牌 → +2 → 跳过/反转 → 黑牌」的优先级挑一张出：功能牌与黑牌留到
   后面更值钱，先出数字牌是标准打发。出黑牌时选**自己手里最多的那个颜色**。

v1.14 起房规可配（`uno_rules`），AI 跟着多两条判断：

- **7 换手必须给出换手对象**。不给的话引擎会把这一手整个拒掉，而人机是
  「一次决策一个动作」—— 被拒就是白扔一轮，严重时整局卡死在那里
  （深空休眠的 `_bed_gate_ok` / `_can_upgrade` 是同一条教训）。换手是**双向**的
  （你把手牌给对方、拿对方那手），所以还要判断这一换划不划算。
- **抢出**是唯一的非本回合动作，走 `best_jump`。

不做的判断（保持简单）：不记牌、不算对手手牌风险、不对 +4 做取舍。
"""

import random
from collections import Counter

from app.game.uno import (
    COLORS, SKIP, REVERSE, DRAW2, SKIP_ALL, DISCARD_ALL, MERCY_LIMIT,
    active_seats, best_color, card_color, card_kind, is_wild,
    jumpable_cards, playable_cards, roulette_seat, team_of,
)

__all__ = ["best_action", "best_jump", "best_color", "random_action"]


def _priority(game: dict, seat: int, card: str) -> int:
    """越小越先出。数字牌最不值钱先甩掉，黑牌留到最后（能变色、能重罚）。

    几个例外：

    - **7** 开着换手房规、而这一换会让自己手牌变多时，排到黑牌后面去
      （换手是你把手牌给对方、拿对方那手，不是白赚）。
    - **弃同色** 手上同色牌够多时**排到最前**（一次甩掉好几张，是这副牌里最强的
      一张）；只有它一张同色牌时不划算，留着等凑堆。
    - **25 张淘汰** 开着、手牌已经过半时，弃同色无条件抢在第一位 —— 那时候
      「少几张」比「留着攒」要紧得多。
    """
    kind = card_kind(card)
    if kind == DISCARD_ALL:
        dumped = _discard_all_count(game, seat, card)
        if game["rules"]["mercy"] and len(game["hands"].get(seat, [])) >= MERCY_LIMIT // 2:
            return 0
        return 0 if dumped >= 2 else 3
    if kind.isdigit():
        base = 0
    elif kind == DRAW2:
        base = 1
    elif kind in (SKIP, REVERSE, SKIP_ALL):
        base = 2
    else:
        base = 3
    if kind == "7" and _swap_hurts(game, seat):
        return 4
    return base


def _discard_all_count(game: dict, seat: int, card: str) -> int:
    """打出这张「弃同色」会**顺带丢掉**几张（不含它自己）。"""
    color = card_color(card)
    return sum(1 for c in game["hands"].get(seat, [])
               if c != card and card_color(c) == color)


def _swap_hurts(game: dict, seat: int) -> bool:
    """这一手 7 打出去，换手会不会让**自己**的手牌变多。

    打出 7 之后自己剩 `len(hand) - 1` 张，然后收到对方那一手；对象由自己挑，
    所以拿「所有对手里最少的那一手」比。
    """
    if game["rules"]["seven_zero"] not in ("swap", "both"):
        return False
    mine = len(game["hands"].get(seat, [])) - 1
    # 比的是**实际能换的那几个人**（组队档里不含队友，见 `_swap_pool`）——
    # 拿队友的手牌数去比，会算出一个根本换不到的目标
    others = [len(game["hands"].get(s, [])) for s in _swap_pool(game, seat)]
    if not others:
        return True
    return min(others) >= mine


def _swap_pool(game: dict, seat: int) -> list[int]:
    """换手对象的候选座位。

    **组队档里不换给队友**：换手是双向的 —— 你把手牌给对方、拿对方那一手，
    拿队友当对象就是「把一手烂牌塞给他、自己拿走他那手」，标准意义上的坑队友
    （房规表里「组队」那一条要的就是这个）。没有对手可换时才退回队友：
    引擎允许（`_can_swap_with` 只看「是不是另一个在局座位」），而**发一个会被
    引擎拒掉的动作在「一次决策一个动作」下就是白扔一轮**（深空休眠两次踩过）。
    """
    others = [s for s in active_seats(game) if s != seat]
    team = team_of(game, seat)
    if team is None:
        return others
    foes = [s for s in others if team_of(game, s) != team]
    return foes or others


def _swap_target(game: dict, seat: int) -> int | None:
    """换给谁：**手牌最少的那个对手**（换完自己拿到的就是最少的那一手）。

    同数量时按座位号取（保证确定性，便于单测）。只剩自己在局时返回 None ——
    那时引擎也不允许换（`_can_swap_with` 要求对象是另一个在局座位）。
    """
    pool = _swap_pool(game, seat)
    if not pool:
        return None
    return min(pool, key=lambda s: (len(game["hands"].get(s, [])), s))


def best_action(game: dict, seat: int) -> dict | None:
    """给该座位挑一个动作；没得挑（不是他的回合/已终局）返回 None。"""
    if game.get("finished") or seat in game.get("out", []):
        return None
    if game.get("turn") != seat:
        return None
    # 颜色轮盘挂起时全场只有他能动，而且只能点名 —— 别的动作引擎一律拒
    if roulette_seat(game) == seat:
        return {"action": "roulette", "color": best_roulette_color(game, seat)}

    options = playable_cards(game, seat)

    # 摸过牌：只有「出刚摸的那张」或「过牌」两条路
    if game.get("drawn_this_turn") is not None:
        if options:
            return _play(game, seat, options[0])
        return {"action": "pass"}

    if options:
        # min 是稳定的：同优先级取先出现的那张，行为可预期
        return _play(game, seat, min(options, key=lambda c: _priority(game, seat, c)))

    # 没牌可出：待罚牌也只能摸（引擎一次摸完累计罚牌），否则摸一张看看
    return {"action": "draw"}


def best_roulette_color(game: dict, seat: int) -> str:
    """颜色轮盘点名：**挑牌堆里剩得最多的那一色**。

    翻牌是「一直翻到出现该色为止」，所以要让它尽量早出现 —— 也就是挑剩得最多的。
    「已经不在牌堆里」的那部分由**自己手里**（私有）与**弃牌堆**（公开）两部分组成，
    两个都数得出来；比较四色里哪个见到的少，就是哪个剩得多。
    **不需要知道整副牌的确切构成**（经典 25/色、无怜悯 34/色，不必去记）。
    """
    seen = Counter(card_color(c) for c in game["hands"].get(seat, [])
                   if not is_wild(c))
    seen += Counter(card_color(c) for c in game["discard_pile"] if not is_wild(c))
    return min(COLORS, key=lambda col: (seen.get(col, 0), COLORS.index(col)))


def best_jump(game: dict, seat: int) -> dict | None:
    """不轮到他时能抢出的一张牌；抢不了（没开房规 / 没有相同牌）返回 None。

    抢出是白赚一手（不占自己的回合），所以默认「能抢就抢」。唯一要克制的是 **7**：
    换手会把自己这一手换成对方的，只有换完更少才值得抢。

    **调用方必须在动作真正落地的时刻重新校验一次**（`uno.can_jump`）—— 抢出是
    竞态的：从「决定要抢」到「真的抢」之间，牌堆顶可能已经被别人抢掉了。
    """
    cards = jumpable_cards(game, seat)
    if not cards:
        return None
    # 能抢的牌彼此完全相同（都等于牌堆顶那张），取哪一张都一样
    card = cards[0]
    if card_kind(card) == "7" and game["rules"]["seven_zero"] in ("swap", "both"):
        target = _swap_target(game, seat)
        mine = len(game["hands"].get(seat, []))
        if target is None or len(game["hands"].get(target, [])) >= mine:
            return None
        act = _play(game, seat, card)
        act["target"] = target
        return act
    return _play(game, seat, card)


def random_action(game: dict, seat: int) -> dict | None:
    """随机走子（单测用）。用于引擎的自对弈模糊测试，不用于对局。"""
    if game.get("finished") or seat in game.get("out", []):
        return None
    if game.get("turn") != seat:
        return None
    if roulette_seat(game) == seat:
        return {"action": "roulette", "color": best_roulette_color(game, seat)}
    options = playable_cards(game, seat)
    if game.get("drawn_this_turn") is not None:
        return _play(game, seat, options[0]) if options else {"action": "pass"}
    if options and random.random() < 0.9:
        return _play(game, seat, random.choice(options))
    return {"action": "draw"}


def _play(game: dict, seat: int, card: str) -> dict:
    """构造出牌动作。

    **黑牌的选色与 7 的换手对象都必须在出牌之前给出来** —— 少一样引擎就把这一手
    整个拒掉。手牌要先去掉这张再算选色（出的那张已经不在手里了）。
    """
    rest = list(game["hands"].get(seat, []))
    rest.remove(card)
    act: dict = {
        "action": "play",
        "card": card,
        "color": best_color(rest) if is_wild(card) else None,
    }
    if card_kind(card) == "7" and game["rules"]["seven_zero"] in ("swap", "both"):
        act["target"] = _swap_target(game, seat)
    return act
