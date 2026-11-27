"""Three-seat Dou Dizhu rules, private views, and public results."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from random import Random


_RANKS = ("3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A", "2")
_SUITS = "CDHS"
_SMALL_JOKER, _BIG_JOKER = len(_RANKS), len(_RANKS) + 1
_ACE = _RANKS.index("A")
_CARD_RANK = {rank + suit: level for level, rank in enumerate(_RANKS) for suit in _SUITS}
_CARD_RANK.update(SJ=_SMALL_JOKER, BJ=_BIG_JOKER)
_CARD_ORDER = {card: index for index, card in enumerate(_CARD_RANK)}

_CLASSIC = {"all_pass": "redeal", "airplane_pair_as_singles": True, "four_pair_as_singles": True}
PRESETS = {
    "classic": {
        "name": "经典", "blurb": "三人都不叫则重发，飞机单翼和四带两单都可以带一对。",
        "rules": _CLASSIC,
    },
    "strict": {
        "name": "严格带牌", "blurb": "三人都不叫则重发，飞机单翼和四带两单的点数必须各异。",
        "rules": {**_CLASSIC, "airplane_pair_as_singles": False, "four_pair_as_singles": False},
    },
}


def normalize_rules(rules: dict | None) -> dict:
    if rules is not None and not isinstance(rules, dict):
        raise ValueError("斗地主房规需要使用对象")
    result = _CLASSIC.copy()
    for key, value in (rules or {}).items():
        if key not in result:
            raise ValueError(f"没有这条房规：{key}")
        if key == "all_pass":
            if value not in ("redeal", "first_bidder"):
                raise ValueError("没有这个全员不叫处理方式")
        elif not isinstance(value, bool):
            raise ValueError("带牌房规只能开启或关闭")
        result[key] = value
    return result


def preset_of(rules: dict) -> str:
    return next((key for key, preset in PRESETS.items() if preset["rules"] == rules), "custom")


@dataclass(frozen=True)
class _Pattern:
    label: str
    body_count: int
    min_sequence: int = 1
    wings: int = 0
    wing_size: int = 1
    pair_as_singles: str | None = None


_PATTERNS = {
    "single": _Pattern("单张", 1),
    "pair": _Pattern("对子", 2),
    "triple": _Pattern("三张", 3),
    "triple_single": _Pattern("三带一", 3, wings=1),
    "triple_pair": _Pattern("三带一对", 3, wings=1, wing_size=2),
    "straight": _Pattern("顺子", 1, min_sequence=5),
    "pair_straight": _Pattern("连对", 2, min_sequence=3),
    "airplane": _Pattern("飞机", 3, min_sequence=2),
    "airplane_single": _Pattern("飞机带单", 3, min_sequence=2, wings=1,
                               pair_as_singles="airplane_pair_as_singles"),
    "airplane_pair": _Pattern("飞机带对", 3, min_sequence=2, wings=1, wing_size=2),
    "four_two_singles": _Pattern("四带两单", 4, wings=2, pair_as_singles="four_pair_as_singles"),
    "four_two_pairs": _Pattern("四带两对", 4, wings=2, wing_size=2),
    "bomb": _Pattern("炸弹", 4),
}


def _sort(cards):
    return sorted(cards, key=_CARD_ORDER.__getitem__, reverse=True)


def _group_by_rank(cards):
    by_rank = defaultdict(list)
    for card in _sort(cards):
        by_rank[_CARD_RANK[card]].append(card)
    return by_rank


def _take_wings(by_rank, body, pattern, groups, rules):
    if not pattern.wings:
        return []
    needed = groups * pattern.wings * pattern.wing_size
    limit = 2 if rules.get(pattern.pair_as_singles) else 1
    cards = []
    for rank in reversed(by_rank):
        if rank in body or (rank == _BIG_JOKER and _SMALL_JOKER in by_rank):
            continue
        if pattern.wing_size == 2:
            if len(by_rank[rank]) >= 2:
                cards.extend(by_rank[rank][:2])
        else:
            cards.extend(by_rank[rank][:min(limit, needed - len(cards))])
        if len(cards) == needed:
            return _sort(cards)
    return None


def _find_cards(by_rank, kind, size, rules, after_rank=-1):
    """Find the lowest qualifying body and fill its wings from other ranks."""
    if kind == "rocket":
        if size == 2 and _SMALL_JOKER in by_rank and _BIG_JOKER in by_rank:
            return _BIG_JOKER, ["BJ", "SJ"]
        return None

    pattern = _PATTERNS[kind]
    groups, remainder = divmod(size, pattern.body_count + pattern.wings * pattern.wing_size)
    sequence = pattern.min_sequence > 1
    if remainder or groups < pattern.min_sequence or (not sequence and groups != 1):
        return None
    highest_rank = _ACE if sequence else _BIG_JOKER
    for high_rank in range(max(groups - 1, after_rank + 1), highest_rank + 1):
        body = range(high_rank - groups + 1, high_rank + 1)
        if any(len(by_rank.get(rank, ())) < pattern.body_count for rank in body):
            continue
        wings = _take_wings(by_rank, body, pattern, groups, rules)
        if wings is not None:
            main_cards = [card for rank in reversed(body) for card in by_rank[rank][:pattern.body_count]]
            return high_rank, main_cards + wings
    return None


def _classify(cards, rules):
    # Searching for the input length uses every selected card, so the same
    # pattern search can also find legal subsets of a full hand for hints.
    by_rank = _group_by_rank(cards)
    for kind in (*_PATTERNS, "rocket"):
        found = _find_cards(by_rank, kind, len(cards), rules)
        if found is not None:
            rank, ordered = found
            return kind, rank, ordered
    return None


def _next_seat(gs, user_id):
    seats = gs["seat_uids"]
    return seats[(seats.index(user_id) + 1) % len(seats)]


def _available_bids(gs):
    return [0, *range(gs["bid"] + 1, 4)]


def _deal(gs, first_bidder):
    deck = list(_CARD_RANK)
    gs["rng"].shuffle(deck)
    gs.update(
        current_id=first_bidder,
        first_bidder=first_bidder,
        bid=0,
        high_bidder=None,
        hands={uid: _sort(deck[i * 17:(i + 1) * 17]) for i, uid in enumerate(gs["seat_uids"])},
        bottom=_sort(deck[51:]),
        actions=[None, None, None],
    )


def create_game(seat_uids: list[int], rules: dict | None, match_id: str, rng: Random) -> dict:
    if (not isinstance(seat_uids, list) or len(seat_uids) != 3
            or any(type(uid) is not int for uid in seat_uids) or len(set(seat_uids)) != 3):
        raise ValueError("斗地主需要三名不同玩家")
    seats = seat_uids.copy()
    gs = {
        "match_id": match_id,
        "revision": 0,
        "phase": "bidding",
        "finished": False,
        "seat_uids": seats,
        "rules": normalize_rules(rules),
        "rng": rng,
        "landlord_id": None,
        "last_play": None,
        "play_history": [],
        "last_rank": None,
        "passes": 0,
        "play_counts": {uid: 0 for uid in seats},
        "multiplier": 1,
        "bombs": 0,
        "rockets": 0,
        "winner": None,
        "reason": None,
        "scores": None,
        "spring": None,
    }
    _deal(gs, rng.choice(seats))
    return gs


def _require_turn(gs, user_id, phase):
    if gs["phase"] != phase:
        raise ValueError("当前阶段不能进行此操作")
    if type(user_id) is not int or gs["current_id"] != user_id:
        raise ValueError("还没轮到你")


def _record_action(gs, user_id, action):
    gs["actions"][gs["seat_uids"].index(user_id)] = action
    gs["revision"] += 1


def _start_play(gs, landlord):
    gs["phase"] = "playing"
    gs["landlord_id"] = gs["current_id"] = landlord
    gs["hands"][landlord] = _sort(gs["hands"][landlord] + gs["bottom"])
    gs["actions"] = [None, None, None]


def bid(gs, user_id, bid):
    _require_turn(gs, user_id, "bidding")
    if type(bid) is not int or bid not in _available_bids(gs):
        raise ValueError("请选择不叫，或叫比当前最高分更高的分数")
    _record_action(gs, user_id, {"kind": "bid", "bid": bid})
    if bid:
        gs["bid"] = bid
        gs["high_bidder"] = user_id

    next_player = _next_seat(gs, user_id)
    if bid == 3:
        _start_play(gs, user_id)
    elif next_player != gs["first_bidder"]:
        gs["current_id"] = next_player
    elif gs["high_bidder"] is not None:
        _start_play(gs, gs["high_bidder"])
    elif gs["rules"]["all_pass"] == "first_bidder":
        gs["bid"] = 1
        _start_play(gs, gs["first_bidder"])
    else:
        _deal(gs, _next_seat(gs, gs["first_bidder"]))


def _active_play(gs):
    return gs["last_play"] if gs["passes"] < 2 else None


def _beats(kind, rank, size, previous, previous_rank):
    earlier_kind = previous["kind"]
    if earlier_kind == "rocket":
        return False
    if kind == "rocket" or (kind == "bomb" and earlier_kind != "bomb"):
        return True
    return kind == earlier_kind and size == len(previous["cards"]) and rank > previous_rank


def play(gs, user_id, cards):
    _require_turn(gs, user_id, "playing")
    hand = gs["hands"][user_id]
    if not isinstance(cards, list) or not 1 <= len(cards) <= len(hand):
        raise ValueError("请选择要出的手牌")
    if any(not isinstance(card, str) or card not in hand for card in cards) or len(set(cards)) != len(cards):
        raise ValueError("请选择手里不同的牌")
    combo = _classify(cards, gs["rules"])
    if combo is None:
        raise ValueError("这手牌不符合本局牌型规则")
    kind, rank, ordered = combo
    previous = _active_play(gs)
    if previous is not None and not _beats(kind, rank, len(cards), previous, gs["last_rank"]):
        raise ValueError(f"需要能压过「{previous['label']}」的牌")

    played = set(cards)
    gs["hands"][user_id] = [card for card in hand if card not in played]
    gs["last_play"] = {"player_id": user_id, "cards": ordered, "kind": kind,
                       "label": "王炸" if kind == "rocket" else _PATTERNS[kind].label}
    gs.setdefault("play_history", []).append({"player_id": user_id, "play": gs["last_play"]})
    gs["last_rank"] = rank
    gs["passes"] = 0
    gs["play_counts"][user_id] += 1
    if kind in ("bomb", "rocket"):
        gs["bombs" if kind == "bomb" else "rockets"] += 1
        gs["multiplier"] *= 2
    _record_action(gs, user_id, {"kind": "play"})
    if gs["hands"][user_id]:
        gs["current_id"] = _next_seat(gs, user_id)
    else:
        winner = "landlord" if user_id == gs["landlord_id"] else "farmers"
        _finish(gs, winner, "empty_hand")


def pass_turn(gs, user_id):
    _require_turn(gs, user_id, "playing")
    if _active_play(gs) is None:
        raise ValueError("领出时必须出牌")
    gs.setdefault("play_history", []).append({"player_id": user_id, "play": None})
    gs["passes"] += 1
    gs["current_id"] = _next_seat(gs, user_id)
    _record_action(gs, user_id, {"kind": "pass"})


def _reply_options(by_rank, size, kind, previous_rank, rules):
    if kind == "rocket":
        return
    if found := _find_cards(by_rank, kind, size, rules, previous_rank):
        yield kind, found[1]
    if kind != "bomb" and (found := _find_cards(by_rank, "bomb", 4, rules)):
        yield "bomb", found[1]
    if found := _find_cards(by_rank, "rocket", 2, rules):
        yield "rocket", found[1]


def hint(gs, user_id) -> list[str]:
    _require_turn(gs, user_id, "playing")
    hand = gs["hands"][user_id]
    rules = gs["rules"]
    previous = _active_play(gs)
    if previous is None:
        combo = _classify(hand, rules)
        return combo[2] if combo else hand[-1:]
    options = _reply_options(_group_by_rank(hand), len(previous["cards"]),
                             previous["kind"], gs["last_rank"], rules)
    return next(options, (None, []))[1]


def _finish(gs, winner, reason):
    gs.update(phase="finished", finished=True, current_id=None, winner=winner, reason=reason)
    if reason != "empty_hand":
        return
    landlord = gs["landlord_id"]
    farmers = [uid for uid in gs["seat_uids"] if uid != landlord]
    if winner == "landlord" and all(gs["play_counts"][uid] == 0 for uid in farmers):
        gs["spring"] = "spring"
    elif winner == "farmers" and gs["play_counts"][landlord] == 1:
        gs["spring"] = "reverse"
    if gs["spring"] is not None:
        gs["multiplier"] *= 2

    points = gs["bid"] * gs["multiplier"]
    farmer_score = -points if winner == "landlord" else points
    gs["scores"] = {uid: -2 * farmer_score if uid == landlord else farmer_score for uid in gs["seat_uids"]}


def depart(gs, user_id, reason):
    if gs["finished"] or type(user_id) is not int or user_id not in gs["seat_uids"]:
        raise ValueError("你不在这局游戏中")
    if reason not in ("surrender", "leave", "disconnect"):
        raise ValueError("未知的离局原因")
    if gs["phase"] == "bidding":
        _finish(gs, None, "canceled")
    else:
        winner = "farmers" if user_id == gs["landlord_id"] else "landlord"
        _finish(gs, winner, reason)
    gs["revision"] += 1


def _public_state(gs):
    return {
        "match_id": gs["match_id"],
        "revision": gs["revision"],
        "seat_uids": gs["seat_uids"].copy(),
        "landlord_id": gs["landlord_id"],
        "remaining": [len(gs["hands"][uid]) for uid in gs["seat_uids"]],
        "bid": gs["bid"],
        "multiplier": gs["multiplier"],
        "rules": gs["rules"].copy(),
    }


def view_for(gs, user_id) -> dict:
    current = gs["current_id"]
    uid = user_id if type(user_id) is int else None
    active = current is not None and current == uid
    return {
        **_public_state(gs),
        "game": "doudizhu",
        "phase": gs["phase"],
        "current_id": current,
        "hand": gs["hands"].get(uid, []).copy(),
        "bottom": gs["bottom"].copy() if gs["landlord_id"] is not None else [],
        "available_bids": _available_bids(gs) if active and gs["phase"] == "bidding" else [],
        "can_pass": active and gs["phase"] == "playing" and _active_play(gs) is not None,
        "last_play": deepcopy(_active_play(gs)),
        "play_history": deepcopy(gs.get("play_history", [])),
        "actions": deepcopy(gs["actions"]),
    }


def snapshot(gs) -> dict | None:
    if not gs["finished"]:
        return None
    return {
        **_public_state(gs),
        "winner": gs["winner"],
        "reason": gs["reason"],
        "bombs": gs["bombs"],
        "rockets": gs["rockets"],
        "spring": gs["spring"],
        "scores": deepcopy(gs["scores"]),
        "last_play": deepcopy(gs["last_play"]),
        "play_history": deepcopy(gs.get("play_history", [])),
    }
