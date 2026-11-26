"""Guandan opponents use the same private view as a seated player."""

from collections import Counter

from app.game.guandan_cards import is_wild, opening_candidates, resolve, strength, suggest


def _play(combo):
    return {"action": "play", "cards": [card["id"] for card in combo["cards"]]}


def _choose_return(view, difficulty):
    hand, level = view["hand"], view["level"]
    eligible = set(view["return_choices"])
    if not eligible:
        return None
    counts = Counter(card["rank"] for card in hand)
    card = min((card for card in hand if card["id"] in eligible), key=lambda c: (
        difficulty == "hard" and counts[c["rank"]] > 1, strength(c["rank"], level), c["id"],
    ))
    return {"action": "return", "card": card["id"]}


def decide(view, uid, difficulty="normal"):
    if view["phase"] == "returning":
        return _choose_return(view, difficulty)
    if view["phase"] != "playing" or view["current_id"] != uid:
        return None
    hand, level, previous = view["hand"], view["level"], view["last_play"]
    try:
        return _play(resolve(hand, level, previous))
    except ValueError:
        pass

    seat = view["players"].index(uid)
    partner = (seat + 2) % 4
    left, right = view["remaining"][(seat + 3) % 4], view["remaining"][(seat + 1) % 4]
    danger = any(count is not None and 0 < count <= 3 for count in (left, right))
    if previous:
        if difficulty != "easy" and previous["uid"] == view["players"][partner]:
            return {"action": "pass"}
        reply = suggest(hand, level, previous)
        if reply is None or (difficulty == "hard" and not danger and len(hand) > 5
                             and reply["kind"] in ("bomb", "straight_flush", "joker_bomb")):
            return {"action": "pass"}
        return _play(reply)
    if difficulty == "easy":
        return _play(suggest(hand, level, None))

    choices = list(opening_candidates(hand, level))
    if difficulty == "normal":
        return _play(max(choices, key=lambda c: (len(c["cards"]), -strength(c["rank"], level))))

    partner_count = view["remaining"][partner]
    if not danger and partner_count in (1, 2):
        feeds = [c for c in choices if len(c["cards"]) == partner_count]
        if feeds:
            return _play(min(feeds, key=lambda c: strength(c["rank"], level)))

    counts = Counter(card["rank"] for card in hand if not is_wild(card, level))

    def score(combo):
        broken = {card["rank"] for card in combo["cards"] if counts[card["rank"]] >= 4}
        wilds = sum(is_wild(card, level) for card in combo["cards"])
        return 2 * len(combo["cards"]) - 3 * (len(broken) + wilds), -strength(combo["rank"], level)

    return _play(max(choices, key=score))
