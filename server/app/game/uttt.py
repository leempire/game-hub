"""封盘版终极井字棋纯引擎。小盘、格均为行优先的 0..8。"""

LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6),
    (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6),
)


def create_game() -> dict:
    return {
        "boards": [[0] * 9 for _ in range(9)],
        "local_results": [0] * 9,
        "active_board": None,
        "next": "black",
        "winner": None,
        "reason": None,
        "revision": 0,
        "moves": [],
    }


def legal_moves(gs: dict) -> list[tuple[int, int]]:
    if gs["winner"] is not None:
        return []
    active = gs["active_board"]
    boards = (active,) if active is not None and gs["local_results"][active] == 0 else range(9)
    return [(b, c) for b in boards if gs["local_results"][b] == 0
            for c in range(9) if gs["boards"][b][c] == 0]


def _result(cells: list[int]) -> int:
    for a, b, c in LINES:
        if cells[a] in (1, 2) and cells[a] == cells[b] == cells[c]:
            return cells[a]
    return 0 if 0 in cells else 3


def apply_move(gs: dict, board: int, cell: int, color: str) -> str | None:
    """成功原地更新；失败返回中文错误，且不触碰任何状态。"""
    if type(board) is not int or type(cell) is not int or not (0 <= board < 9 and 0 <= cell < 9):
        return "小盘和格子必须是 0 到 8 的整数"
    if gs["winner"] is not None:
        return "对局已结束"
    if color not in ("black", "white"):
        return "无效的棋子颜色"
    if color != gs["next"]:
        return "还没轮到你落子"
    if gs["local_results"][board] != 0:
        return "该小盘已结束"
    active = gs["active_board"]
    if active is not None and gs["local_results"][active] == 0 and board != active:
        return "请在指定的小盘落子"
    if gs["boards"][board][cell] != 0:
        return "该格已有棋子"

    gs["boards"][board][cell] = 1 if color == "black" else 2
    gs["local_results"][board] = _result(gs["boards"][board])
    result = _result(gs["local_results"])
    if result:
        gs["winner"] = {1: "black", 2: "white", 3: "draw"}[result]
        gs["reason"] = "draw" if result == 3 else "three_boards"
    # 必须在封盘结算后再指派：本手封盘并指回自身时，对手自由选盘。
    gs["active_board"] = cell if not result and gs["local_results"][cell] == 0 else None
    gs["next"] = "white" if color == "black" else "black"
    gs["revision"] += 1
    gs["moves"].append({"board": board, "cell": cell, "color": color})
    return None


def _public_state(gs: dict) -> dict:
    moves = [dict(move) for move in gs["moves"]]
    return {
        "boards": [board[:] for board in gs["boards"]],
        "local_results": gs["local_results"][:],
        "active_board": gs["active_board"],
        "next": gs["next"],
        "winner": gs["winner"],
        "reason": gs["reason"],
        "revision": gs["revision"],
        "moves": moves,
        "last_move": dict(moves[-1]) if moves else None,
    }


def game_to_sync(gs: dict, user_id: int) -> dict:
    """逐人身份与每个合法动作的权威预览，所有可变字段均为副本。"""
    view = _public_state(gs)
    colors = gs.get("player_colors", {})
    view["your_color"] = colors.get(user_id, colors.get(str(user_id), ""))
    view["legal"] = []
    for board, cell in legal_moves(gs):
        after = _public_state(gs)
        apply_move(after, board, cell, gs["next"])
        view["legal"].append({
            "board": board, "cell": cell,
            "next_board": after["active_board"], "winner": after["winner"],
        })
    return view


def snapshot(gs: dict) -> dict:
    """历史最终盘面不携带连接身份或其他房间内部字段。"""
    return {"game": "uttt", **_public_state(gs)}
