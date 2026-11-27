"""Four-seat Guandan match rules and private player views."""

from copy import deepcopy
from random import Random

from app.game.guandan_cards import JOKERS, RANKS, SUITS, is_wild, resolve, strength, suggest


_RETURN_RANKS = frozenset(RANKS[:RANKS.index("10") + 1])


def _sort_hand(hand, level):
    hand.sort(key=lambda card: (-strength(card["rank"], level), card["id"]))


def _deal_hands(players, level, rng):
    faces = [(rank, suit) for rank in RANKS for suit in SUITS] + [(rank, "") for rank in JOKERS]
    cards = [{"id": index, "rank": rank, "suit": suit} for index, (rank, suit) in enumerate(faces * 2)]
    rng.shuffle(cards)
    hands = {uid: cards[index * 27:(index + 1) * 27] for index, uid in enumerate(players)}
    for hand in hands.values():
        _sort_hand(hand, level)
    return hands


def create_game(players, match_id, rng=None):
    players = list(players)
    if len(players) != 4 or any(type(uid) is not int for uid in players) or len(set(players)) != 4:
        raise ValueError("掼蛋需要四位不同的玩家")
    rng = Random() if rng is None else rng
    return {
        "players": players,
        "match_id": match_id,
        "revision": 0,
        "deal": 1,
        "phase": "playing",
        "levels": ["2", "2"],
        "level": "2",
        "level_team": None,
        "hands": _deal_hands(players, "2", rng),
        "current_id": rng.choice(players),
        "passed": [],
        "finish_order": [],
        "last_play": None,
        "play_history": [],
        "lead_reason": "first",
        "exchange": None,
        "return_submissions": {},
        "deal_result": None,
        "winner": None,
        "reason": None,
    }


def _team(gs, uid):
    return gs["players"].index(uid) % 2


def _partner(gs, uid):
    return gs["players"][(gs["players"].index(uid) + 2) % 4]


def _next_player(gs, uid):
    start = gs["players"].index(uid)
    for offset in range(1, 5):
        candidate = gs["players"][(start + offset) % 4]
        if gs["hands"][candidate]:
            return candidate


def _require_player(gs, uid):
    if type(uid) is not int or uid not in gs["players"]:
        raise ValueError("你不在本场掼蛋中")


def _require_turn(gs, uid):
    _require_player(gs, uid)
    if gs["phase"] != "playing" or gs["current_id"] != uid:
        raise ValueError("现在不能出牌，等轮到你再操作")


def preview(gs, uid, cards):
    _require_turn(gs, uid)
    if not isinstance(cards, list) or not cards or any(type(card_id) is not int for card_id in cards):
        raise ValueError("请选择手牌再出牌")
    selected = set(cards)
    if len(selected) != len(cards):
        raise ValueError("选牌中有重复的牌")
    hand = [card for card in gs["hands"][uid] if card["id"] in selected]
    if len(hand) != len(selected):
        raise ValueError("选中的牌不在你的手牌中")
    return deepcopy(resolve(hand, gs["level"], gs["last_play"]))


def hint(gs, uid):
    _require_turn(gs, uid)
    return deepcopy(suggest(gs["hands"][uid], gs["level"], gs["last_play"]))


def _finish_deal(gs):
    order = gs["finish_order"]
    winner = _team(gs, order[0])
    partner = _partner(gs, order[0])
    advance = 4 - order.index(partner)
    level = RANKS.index(gs["levels"][winner]) + advance
    gs["levels"][winner] = RANKS[min(len(RANKS) - 1, level)]
    gs["deal_result"] = {
        "deal": gs["deal"], "winner": winner, "finish_order": order.copy(),
        "levels": gs["levels"].copy(), "advance": advance, "level": gs["level"],
    }
    if gs["level"] == "A" and gs["level_team"] == winner and advance >= 2:
        gs.update(phase="finished", winner=winner, reason="clear_ace", current_id=None)
    else:
        gs.update(phase="between", current_id=None)


def _record_play(gs, uid, play):
    gs.setdefault("play_history", []).append({
        "player_id": uid, "deal": gs["deal"], "level": gs["level"], "play": play,
    })


def _play(gs, uid, ids):
    combo = preview(gs, uid, ids)
    selected = set(ids)
    gs["hands"][uid] = [card for card in gs["hands"][uid] if card["id"] not in selected]
    gs.update(last_play={"uid": uid, **combo}, passed=[], lead_reason=None)
    _record_play(gs, uid, gs["last_play"])
    order = gs["finish_order"]
    if not gs["hands"][uid]:
        order.append(uid)
        if len(order) == 2 and _team(gs, order[0]) == _team(gs, order[1]):
            _finish_deal(gs)
            return
        if len(order) == 3:
            order.append(next(player for player in gs["players"] if player not in order))
            _finish_deal(gs)
            return
    gs["current_id"] = _next_player(gs, uid)


def _pass(gs, uid):
    _require_turn(gs, uid)
    if gs["last_play"] is None:
        raise ValueError("领出时必须出牌")
    _record_play(gs, uid, None)
    last_uid = gs["last_play"]["uid"]
    gs["passed"].append(uid)
    waiting = any(player != last_uid and gs["hands"][player] and player not in gs["passed"]
                  for player in gs["players"])
    if waiting:
        gs["current_id"] = _next_player(gs, uid)
    elif gs["hands"][last_uid]:
        gs.update(current_id=last_uid, last_play=None, passed=[], lead_reason="pass")
    else:
        gs.update(current_id=_partner(gs, last_uid), last_play=None, passed=[], lead_reason="partner")


def _tribute_exchange(players, hands, order, level):
    head = order[0]
    donors = [uid for uid in players if uid not in order] if len(order) == 2 else order[-1:]
    if sum(card["rank"] == "BJ" for uid in donors for card in hands[uid]) == 2:
        return {"antitribute": True, "lead_id": head, "pairs": []}

    tributes = {uid: next(card for card in hands[uid] if not is_wild(card, level)) for uid in donors}

    # In a double tribute tie, the head receives from the player on their right.
    lower_seat = players[(players.index(head) + 1) % 4]
    donors.sort(key=lambda uid: (strength(tributes[uid]["rank"], level), uid == lower_seat), reverse=True)
    pairs = []
    for donor, recipient in zip(donors, order):
        tribute = tributes[donor]
        hands[donor].remove(tribute)
        hands[recipient].append(tribute)
        _sort_hand(hands[recipient], level)
        pairs.append({"from_id": donor, "to_id": recipient, "tribute": tribute,
                      "returned": None, "submitted": False})
    return {"antitribute": False, "lead_id": donors[0], "pairs": pairs}


def _next_deal(gs, rng):
    if gs["phase"] != "between":
        raise ValueError("本副结束后才能继续下一副")
    if rng is not None and not isinstance(rng, Random):
        raise ValueError("发牌随机源无效")
    completed = gs["deal_result"]
    level_team = completed["winner"]
    level = gs["levels"][level_team]
    hands = _deal_hands(gs["players"], level, Random() if rng is None else rng)
    exchange = _tribute_exchange(gs["players"], hands, completed["finish_order"], level)
    resisted = exchange["antitribute"]
    gs.update(
        deal=gs["deal"] + 1, phase="playing" if resisted else "returning", level=level, level_team=level_team,
        hands=hands, exchange=exchange, return_submissions={}, finish_order=[], passed=[], last_play=None,
        current_id=exchange["lead_id"] if resisted else None,
        lead_reason="antitribute" if resisted else None,
    )


def _return_choices(gs, uid):
    if gs["phase"] != "returning" or not any(pair["to_id"] == uid and not pair["submitted"]
                                             for pair in gs["exchange"]["pairs"]):
        return []
    hand, level = gs["hands"][uid], gs["level"]
    choices = [card["id"] for card in hand if card["rank"] in _RETURN_RANKS and card["rank"] != level]
    if choices:
        return choices
    weakest = strength(hand[-1]["rank"], level)
    return [card["id"] for card in hand if strength(card["rank"], level) == weakest]


def _return_card(gs, uid, card_id):
    if type(card_id) is not int or card_id not in _return_choices(gs, uid):
        raise ValueError("请选择一张可还贡的手牌")
    exchange = gs["exchange"]
    pair = next(pair for pair in exchange["pairs"] if pair["to_id"] == uid)
    pair["submitted"] = True
    gs["return_submissions"][uid] = card_id
    if not all(pair["submitted"] for pair in exchange["pairs"]):
        return

    for pair in exchange["pairs"]:
        giver = pair["to_id"]
        hand = gs["hands"][giver]
        returned = next(card for card in hand if card["id"] == gs["return_submissions"][giver])
        pair["returned"] = returned
        hand.remove(returned)
        recipient = gs["hands"][pair["from_id"]]
        recipient.append(returned)
        _sort_hand(recipient, gs["level"])
    gs.update(phase="playing", current_id=exchange["lead_id"], lead_reason="tribute", return_submissions={})


def apply(gs, uid, action, data):
    _require_player(gs, uid)
    if not isinstance(data, dict):
        raise ValueError("无效的掼蛋操作数据")
    if action == "play":
        _play(gs, uid, data.get("cards"))
    elif action == "pass":
        _pass(gs, uid)
    elif action == "return":
        _return_card(gs, uid, data.get("card"))
    elif action == "next":
        _next_deal(gs, data.get("rng"))
    else:
        raise ValueError("未知的掼蛋操作")
    gs["revision"] += 1


def depart(gs, uid, reason):
    _require_player(gs, uid)
    if gs["phase"] == "finished":
        raise ValueError("本场掼蛋已经结束")
    if reason not in ("surrender", "leave", "disconnect"):
        raise ValueError("未知的离局原因")
    gs.update(phase="finished", current_id=None, winner=1 - _team(gs, uid), reason=reason, return_submissions={})
    gs["revision"] += 1


def _remaining(gs):
    return [len(gs["hands"][player]) for player in gs["players"]]


def view_for(gs, uid):
    uid = uid if type(uid) is int else None
    public = {key: gs[key] for key in (
        "match_id", "revision", "phase", "deal", "players", "levels", "level", "level_team", "current_id",
        "passed", "finish_order", "last_play", "exchange", "lead_reason", "deal_result",
    )}
    return deepcopy({
        "game": "guandan",
        **public,
        "hand": gs["hands"].get(uid, []),
        "remaining": _remaining(gs),
        "play_history": gs.get("play_history", []),
        "return_choices": _return_choices(gs, uid),
    })


def snapshot(gs):
    return deepcopy({
        **{key: gs[key] for key in ("match_id", "players", "levels", "deal", "winner", "reason", "deal_result")},
        "remaining": _remaining(gs),
        "play_history": gs.get("play_history", []),
    })
