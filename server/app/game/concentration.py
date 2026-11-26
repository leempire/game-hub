"""Shared memory game. Only the public view may leave the room server."""

MIN_PLAYERS = 2
MAX_PLAYERS = 4
CARD_COUNTS = (16, 24)
DEFAULT_CARD_COUNT = 24
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUITS = ("S", "H", "C", "D")
MATCH_REVEAL_MS = 650
MISS_REVEAL_MS = 1500


def create_game(players: list[int], card_count: int, match_id: str, rng) -> dict:
    if not MIN_PLAYERS <= len(players) <= MAX_PLAYERS or len(set(players)) != len(players):
        raise ValueError("「精神衰弱」需要 2–4 名玩家")
    if type(card_count) is not int or card_count not in CARD_COUNTS:
        raise ValueError("牌数只能选择 16 或 24 张")
    deck = [[rank, suit] for rank in rng.sample(RANKS, card_count // 2)
            for suit in rng.sample(SUITS, 2)]
    rng.shuffle(deck)
    return {
        "match_id": match_id, "revision": 0, "card_count": card_count, "deck": deck,
        "seats": list(players), "turn_id": rng.choice(players),
        "scores": dict.fromkeys(players, 0), "forfeited": set(),
        "revealed": [], "collected": set(), "phase": "first",
        "outcome": None, "reveal_ms": None,
    }


def is_active(state: dict, user_id: int) -> bool:
    return user_id in state["scores"] and user_id not in state["forfeited"]


def flip(state: dict, user_id: int, index: int) -> None:
    if user_id != state["turn_id"] or not is_active(state, user_id):
        raise ValueError("还没轮到你翻牌")
    if state["phase"] == "revealing":
        raise ValueError("请等待两张牌展示完毕")
    if type(index) is not int or not 0 <= index < state["card_count"]:
        raise ValueError("牌位无效")
    if index in state["revealed"] or index in state["collected"]:
        raise ValueError("请翻一张盖着的牌")
    state["revealed"].append(index)
    state["revision"] += 1
    if state["phase"] == "first":
        state["phase"] = "second"
        return
    first, second = state["revealed"]
    matched = state["deck"][first][0] == state["deck"][second][0]
    state.update(phase="revealing", outcome="match" if matched else "miss",
                 reveal_ms=MATCH_REVEAL_MS if matched else MISS_REVEAL_MS)


def _advance_turn(state: dict) -> None:
    seats = state["seats"]
    current = seats.index(state["turn_id"])
    state["turn_id"] = next(uid for uid in seats[current + 1:] + seats[:current + 1]
                            if is_active(state, uid))


def _collect_pair(state: dict) -> None:
    state["collected"].update(state["revealed"])
    state["scores"][state["turn_id"]] += 1


def _finish(state: dict, reason: str) -> None:
    state["deck"] = []
    state["revealed"].clear()
    state.update(turn_id=None, phase="finished", outcome=None, reveal_ms=None,
                 reason=reason, finished=True)


def finish_reveal(state: dict) -> None:
    matched = state["outcome"] == "match"
    if matched:
        _collect_pair(state)
    state["revision"] += 1
    if len(state["collected"]) == state["card_count"]:
        _finish(state, "completed")
        return
    if not matched or not is_active(state, state["turn_id"]):
        _advance_turn(state)
    state["revealed"].clear()
    state.update(phase="first", outcome=None, reveal_ms=None)


def forfeit(state: dict, user_id: int, reason: str) -> bool:
    if state["phase"] == "finished" or not is_active(state, user_id):
        return False
    state["forfeited"].add(user_id)
    state["revision"] += 1
    if len(state["forfeited"]) >= len(state["seats"]) - 1:
        if state["outcome"] == "match":
            _collect_pair(state)
        _finish(state, reason)
    elif state["turn_id"] == user_id and state["phase"] != "revealing":
        state["revealed"].clear()
        state["phase"] = "first"
        _advance_turn(state)
    return True


def score_rows(state: dict) -> list[dict]:
    """Keep display order in seat order and use competition ranks for ties."""
    scores = state["scores"]
    active_scores = [scores[uid] for uid in state["seats"] if is_active(state, uid)]
    rows = []
    for uid in state["seats"]:
        row = {"id": uid, "pairs": scores[uid]}
        if uid in state["forfeited"]:
            row["forfeited"] = True
        if state["phase"] == "finished":
            row["rank"] = (len(active_scores) + 1 if uid in state["forfeited"]
                           else 1 + sum(pair_count > scores[uid] for pair_count in active_scores))
        rows.append(row)
    return rows


def view(state: dict) -> dict:
    slots = []
    for index in range(state["card_count"]):
        if index in state["revealed"]:
            rank, suit = state["deck"][index]
            slots.append({"state": "revealed", "rank": rank, "suit": suit})
        else:
            slots.append({"state": "collected" if index in state["collected"] else "hidden"})
    return {
        "game": "concentration", "match_id": state["match_id"], "revision": state["revision"],
        "slots": slots, "turn_id": state["turn_id"],
        "remaining_pairs": (state["card_count"] - len(state["collected"])) // 2,
        "phase": state["phase"], "outcome": state["outcome"], "scores": score_rows(state),
        **({"reason": state["reason"]} if state["phase"] == "finished" else {}),
    }


def snapshot(state: dict) -> dict:
    return {"game": "concentration", "scores": score_rows(state), "card_count": state["card_count"]}
