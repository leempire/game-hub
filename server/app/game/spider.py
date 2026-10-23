"""单花色蜘蛛纸牌纯引擎；暗牌与余牌点数不下发给客户端。"""

from copy import deepcopy
import random

UNDO_LIMIT = 200
_DEAL_ATTEMPTS = 6
_SEARCH_NODES = 300
_FALLBACK_SEEDS = {8: 1, 12: 5, 16: 21}


def create_game(seed: int | None = None, num_players: int = 1) -> dict:
    if type(num_players) is not int or not 1 <= num_players <= 4:
        raise ValueError("蜘蛛纸牌支持 1–4 人合作")
    total_runs = 8 if num_players == 1 else 12 if num_players == 2 else 16
    rng = random.Random(seed)
    for _ in range(_DEAL_ATTEMPTS):
        game = _shuffled_game(rng, total_runs)
        if _winning_path(game) is not None:
            return game

    # 同时置换等高列及对应余牌，保留已回放验证的兜底牌局的解。
    game = _shuffled_game(random.Random(_FALLBACK_SEEDS[total_runs]), total_runs)
    count, tall = len(game["columns"]), total_runs // 2
    first, rest = list(range(tall)), list(range(tall, count))
    rng.shuffle(first)
    rng.shuffle(rest)
    order = first + rest
    game["columns"] = [game["columns"][i] for i in order]
    game["stock"] = [game["stock"][row + i] for row in range(0, len(game["stock"]), count) for i in order]
    return game


def _shuffled_game(rng: random.Random, total_runs: int = 8) -> dict:
    deck = list(range(1, 14)) * total_runs
    rng.shuffle(deck)
    columns = []
    for i in range(total_runs * 5 // 4):
        size = 6 if i < total_runs // 2 else 5
        columns.append(deck[:size])
        del deck[:size]
    return {"columns": columns, "face_up": [len(c) - 1 for c in columns],
            "stock": deck, "total_runs": total_runs, "completed": 0, "move_count": 0, "revision": 0,
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
    if game["completed"] == game.get("total_runs", 8):
        game.update(finished=True, winner="cooperative", reason="completed")


def _play(game: dict, move: dict | None):
    """执行已验证的移动或发牌，供正式操作与发牌筛选共用。"""
    if move is None:
        for column in game["columns"]:
            column.append(game["stock"].pop(0))
    else:
        source, index, target = move["source"], move["index"], move["target"]
        game["columns"][target].extend(game["columns"][source][index:])
        del game["columns"][source][index:]
        game["face_up"][source] = max(0, min(game["face_up"][source],
                                            len(game["columns"][source]) - 1))
    game["move_count"] += 1
    _settle(game)


def _winning_path(game: dict, node_limit: int | None = None) -> list | None:
    """返回完整通关路径；搜索超限只表示未验证，不表示无解。"""
    if node_limit is None:
        node_limit = _SEARCH_NODES * game.get("total_runs", 8) // 8
    seen, path = set(), []

    def search(state):
        if state["finished"]:
            return list(path)
        if len(seen) >= node_limit or len(path) >= state.get("total_runs", 8) * 32:
            return None
        key = (tuple(map(tuple, state["columns"])), tuple(state["face_up"]), len(state["stock"]))
        if key in seen:
            return None
        seen.add(key)
        options = []
        for move in legal_moves(state):
            source, index, target = move["source"], move["index"], move["target"]
            column, dest = state["columns"][source], state["columns"][target]
            if not dest and index == 0:
                continue
            # shortcut: 启发式会漏掉可解局；需要难度分级时再扩展搜索，不据此宣判无解。
            if index > state["face_up"][source] and column[index - 1] == column[index] + 1:
                if dest or not state["stock"]:
                    continue
            if not dest and any(not c for c in state["columns"][:target]):
                continue
            trial = {**state, "columns": [c[:] for c in state["columns"]],
                     "face_up": state["face_up"][:]}
            _play(trial, move)
            score = (trial["completed"] - state["completed"]) * 100
            score += (sum(state["face_up"]) - sum(trial["face_up"])) * 10
            score += 4 * (not trial["columns"][source]) + 2 * bool(dest) - 4 * (not dest)
            options.append((score, move, trial))
        if state["stock"] and all(state["columns"]):
            trial = {**state, "columns": [c[:] for c in state["columns"]],
                     "face_up": state["face_up"][:], "stock": state["stock"][:]}
            _play(trial, None)
            options.append((-1, None, trial))
        options.sort(key=lambda option: option[0], reverse=True)
        for _, move, trial in options:
            path.append(move)
            result = search(trial)
            if result is not None:
                return result
            path.pop()
            if len(seen) >= node_limit:
                break
        return None

    return search(game)


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
        _play(game, None)
    elif action == "move":
        if any(type(value) is not int for value in (source, index, target)):
            return "请选择有效的牌组和目标列"
        move = {"source": source, "index": index, "target": target}
        if move not in legal_moves(game):
            return "只能移动连续递减的明牌，接到大一点的牌或空列"
        _remember(game)
        _play(game, move)
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
        "total_runs": game.get("total_runs", 8),
        "departed": list(game.get("departed", [])),
        "move_count": game["move_count"], "revision": game["revision"],
        "can_undo": bool(game["history"]) and not game["finished"],
        "can_deal": bool(game["stock"]) and all(game["columns"]) and not game["finished"],
        "legal_moves": legal_moves(game), "finished": game["finished"],
        "winner": game["winner"], "reason": game["reason"],
    }


def snapshot(game: dict) -> dict:
    return {"game": "spider", **game_to_sync(game)}
