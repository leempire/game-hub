"""Rule-based Dou Dizhu opponents using the same private view as a player."""

from app.game import doudizhu as game


def _bid(view, difficulty):
    hand = view["hand"]
    groups = game._group_by_rank(hand)
    power = (3 * ("BJ" in hand) + 2 * ("SJ" in hand)
             + 2 * len(groups.get(game._CARD_RANK["2S"], ()))
             + len(groups.get(game._CARD_RANK["AS"], ()))
             + 3 * sum(len(cards) == 4 for cards in groups.values()))
    high = 9 if difficulty == "easy" else 8
    desired = 3 if power >= high else 2 if power >= 6 else 1 if power >= 4 else 0
    return max(bid for bid in view["available_bids"] if bid <= desired)


def _opening_plays(hand, rules):
    """One low representative per shape and size, using the engine's matcher."""
    groups = game._group_by_rank(hand)
    for kind, shape in game._PATTERNS.items():
        unit = shape.body_count + shape.wings * shape.wing_size
        counts = (1,) if shape.min_sequence == 1 else range(shape.min_sequence, len(hand) // unit + 1)
        for count in counts:
            if found := game._find_cards(groups, kind, count * unit, rules):
                yield kind, found[1]
    if "BJ" in hand and "SJ" in hand:
        yield "rocket", ["BJ", "SJ"]


def decide(view: dict, user_id: int, difficulty: str = "normal") -> dict:
    if user_id != view["current_id"]:
        raise ValueError("还没轮到人机")
    if view["phase"] == "bidding":
        return {"action": "bid", "bid": _bid(view, difficulty)}

    hand, rules, lead = view["hand"], view["rules"], view["last_play"]
    lead_rank = game._classify(lead["cards"], rules)[1] if lead else None
    full = game._classify(hand, rules)
    if full and (not lead or game._beats(full[0], full[1], len(hand), lead, lead_rank)):
        return {"action": "play", "cards": full[2]}

    landlord = view["landlord_id"]
    counts = dict(zip(view["seat_uids"], view["remaining"]))
    threat = min(count for uid, count in counts.items() if uid != user_id and (user_id == landlord or uid == landlord))
    if lead:
        if difficulty != "easy" and user_id != landlord and lead["player_id"] != landlord:
            return {"action": "pass"}
        replies = game._reply_options(game._group_by_rank(hand), len(lead["cards"]), lead["kind"], lead_rank, rules)
        reply = next(replies, None)
        if not reply:
            return {"action": "pass"}
        kind, cards = reply
        if difficulty == "hard" and kind in ("bomb", "rocket") and threat > 3 and len(hand) > 8:
            return {"action": "pass"}
        return {"action": "play", "cards": cards}

    if difficulty == "easy":
        return {"action": "play", "cards": [min(hand, key=game._CARD_ORDER.__getitem__)]}

    def priority(move):
        kind, cards = move
        hard = difficulty == "hard"
        blocks_short_hand = hard and threat <= 2 and len(cards) > threat
        one_move_left = hard and game._classify([card for card in hand if card not in cards], rules) is not None
        return (blocks_short_hand, one_move_left, kind not in ("bomb", "rocket"), len(cards),
                -sum(game._CARD_RANK[card] >= game._CARD_RANK["2S"] for card in cards))

    _, cards = max(_opening_plays(hand, rules), key=priority)
    if difficulty == "hard" and threat == 1 and len(cards) == 1:
        cards = [max(hand, key=game._CARD_ORDER.__getitem__)]
    return {"action": "play", "cards": cards}
