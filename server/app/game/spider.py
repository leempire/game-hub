"""单花色蜘蛛纸牌纯引擎；暗牌与余牌点数不下发给客户端。"""

from copy import deepcopy
import random

UNDO_LIMIT = 200


def create_game(seed: int | None = None) -> dict:
    deck = list(range(1, 14)) * 8
    random.Random(seed).shuffle(deck)
    columns = []
    for i in range(10):
        size = 6 if i < 4 else 5
        columns.append(deck[:size])
        del deck[:size]
    return {"columns": columns, "face_up": [len(c) - 1 for c in columns],
            "stock": deck, "completed": 0, "move_count": 0, "revision": 0,
            "history": [], "finished": False, "winner": None, "reason": None}


def legal_moves(game: dict) -> list[dict]:
    if game["finished"]:
        return []
    moves = []
    for source, column in enumerate(game["columns"]):
        for index in range(len(column) - 1, game["face_up"][source] - 1, -1):
            if index < len(column) - 1 and column[index] != column[index + 1] + 1:
                break
            for target, dest in enumerate(game["columns"]):
                if target != source and (not dest or dest[-1] == column[index] + 1):
                    moves.append({"source": source, "index": index, "target": target})
    return moves


def _remember(game: dict):
    game["history"].append(deepcopy({k: game[k] for k in
        ("columns", "face_up", "stock", "completed", "move_count")}))
    del game["history"][:-UNDO_LIMIT]


def _settle(game: dict):
    for i, column in enumerate(game["columns"]):
        if column:
            game["face_up"][i] = min(game["face_up"][i], len(column) - 1)
        while (len(column) - game["face_up"][i] >= 13
               and column[-13:] == list(range(13, 0, -1))):
            del column[-13:]
            game["completed"] += 1
            game["face_up"][i] = max(0, min(game["face_up"][i], len(column) - 1))
    if game["completed"] == 8:
        game.update(finished=True, winner="solo", reason="completed")


def apply_action(game: dict, action: str, source=None, index=None, target=None) -> str | None:
    """成功返回 None，拒绝返回中文原因；拒绝时不改变任何状态。"""
    if game["finished"]:
        return "本局已结束，请开始新牌局"
    if action == "undo":
        if not game["history"]:
            return "还没有可以撤销的操作"
        game.update(game["history"].pop())
    elif action == "deal":
        if not game["stock"]:
            return "余牌已经发完"
        if any(not column for column in game["columns"]):
            return "请先填满所有空列，再发牌"
        _remember(game)
        for column in game["columns"]:
            column.append(game["stock"].pop(0))
        game["move_count"] += 1
        _settle(game)
    elif action == "move":
        if any(type(value) is not int for value in (source, index, target)):
            return "请选择有效的牌组和目标列"
        move = {"source": source, "index": index, "target": target}
        if move not in legal_moves(game):
            return "只能移动连续递减的明牌，接到大一点的牌或空列"
        _remember(game)
        cards = game["columns"][source][index:]
        del game["columns"][source][index:]
        game["columns"][target].extend(cards)
        game["face_up"][source] = max(0, min(game["face_up"][source],
                                            len(game["columns"][source]) - 1))
        game["move_count"] += 1
        _settle(game)
    else:
        return "不支持的蜘蛛纸牌操作"
    game["revision"] += 1
    return None


def game_to_sync(game: dict) -> dict:
    return {
        "columns": [[rank if i >= game["face_up"][col] else None
                     for i, rank in enumerate(column)]
                    for col, column in enumerate(game["columns"])],
        "stock_count": len(game["stock"]), "completed": game["completed"],
        "move_count": game["move_count"], "revision": game["revision"],
        "can_undo": bool(game["history"]) and not game["finished"],
        "can_deal": bool(game["stock"]) and all(game["columns"]) and not game["finished"],
        "legal_moves": legal_moves(game), "finished": game["finished"],
        "winner": game["winner"], "reason": game["reason"],
    }


def snapshot(game: dict) -> dict:
    return {"game": "spider", **game_to_sync(game)}
