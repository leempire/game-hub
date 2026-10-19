"""Dots and Boxes (点格棋) pure game logic — no I/O, easy to unit-test.

Design decisions (from research):
- 5×5 dots = 4×4 boxes (default). 16 boxes total, good game length.
- Edge model: horizontal edges h[y][x] (0 <= y <= rows, 0 <= x < cols)
  and vertical edges v[y][x] (0 <= y < rows, 0 <= x <= cols).
  For an n×n dot grid, rows = n-1, cols = n-1 boxes.
- Players: 1 (first/black) and 2 (second/white).
- Completing the 4th side of a box claims it and grants an extra turn.
- Game ends when all edges are drawn. Most boxes wins; tie possible.
"""

# Default board: 5×5 dots → 4×4 boxes
DEFAULT_DOTS = 5
PLAYER1 = 1  # "black" / first player
PLAYER2 = 2  # "white" / second player


def create_game(dots: int = DEFAULT_DOTS) -> dict:
    """Create a new Dots and Boxes game state.

    For dots=n, there are (n-1)×(n-1) boxes.
    Horizontal edges: n rows of (n-1) edges each.
    Vertical edges: (n-1) rows of n edges each.
    """
    rows = dots - 1  # number of box rows
    cols = dots - 1  # number of box columns
    # h[y][x]: horizontal edge at row y, col x (y in 0..rows, x in 0..cols-1)
    h = [[0] * cols for _ in range(rows + 1)]
    # v[y][x]: vertical edge at row y, col x (y in 0..rows-1, x in 0..cols)
    v = [[0] * (cols + 1) for _ in range(rows)]
    # boxes[y][x]: owner of box at (y, x), 0 = unclaimed
    boxes = [[0] * cols for _ in range(rows)]
    return {
        "dots": dots,
        "rows": rows,
        "cols": cols,
        "h": h,
        "v": v,
        "boxes": boxes,
        "next": PLAYER1,
        "scores": {PLAYER1: 0, PLAYER2: 0},
        "moves": [],
        "winner": None,
        "reason": None,
    }


def _edge_key(orient: str, y: int, x: int) -> str:
    """Return a string key for an edge: 'h{y},{x}' or 'v{y},{x}'."""
    return f"{orient}{y},{x}"


def is_valid_edge(gs: dict, orient: str, y: int, x: int) -> bool:
    """Check if drawing edge (orient, y, x) is legal."""
    if orient == "h":
        if y < 0 or y > gs["rows"]:
            return False
        if x < 0 or x >= gs["cols"]:
            return False
        return gs["h"][y][x] == 0
    elif orient == "v":
        if y < 0 or y >= gs["rows"]:
            return False
        if x < 0 or x > gs["cols"]:
            return False
        return gs["v"][y][x] == 0
    return False


def get_legal_edges(gs: dict) -> list[tuple[str, int, int]]:
    """Return all legal empty edges as (orient, y, x) tuples."""
    edges = []
    for y in range(gs["rows"] + 1):
        for x in range(gs["cols"]):
            if gs["h"][y][x] == 0:
                edges.append(("h", y, x))
    for y in range(gs["rows"]):
        for x in range(gs["cols"] + 1):
            if gs["v"][y][x] == 0:
                edges.append(("v", y, x))
    return edges


def _box_sides(gs: dict, by: int, bx: int) -> tuple[int, int, int, int]:
    """Return (top, bottom, left, right) edge values for box (by, bx)."""
    top = gs["h"][by][bx]
    bottom = gs["h"][by + 1][bx]
    left = gs["v"][by][bx]
    right = gs["v"][by][bx + 1]
    return top, bottom, left, right


def _count_sides(gs: dict, by: int, bx: int) -> int:
    """Count how many sides of box (by, bx) are drawn (non-zero)."""
    t, b, l, r = _box_sides(gs, by, bx)
    return (1 if t else 0) + (1 if b else 0) + (1 if l else 0) + (1 if r else 0)


def _find_completed_boxes(
    gs: dict, orient: str, y: int, x: int,
) -> list[tuple[int, int]]:
    """Find boxes completed by placing edge (orient, y, x).

    A horizontal edge h[y][x] is the top of box (y, x) and bottom of box (y-1, x).
    A vertical edge v[y][x] is the left of box (y, x) and right of box (y, x-1).
    """
    completed = []
    if orient == "h":
        # Box below: (y, x) — this edge is its top
        if y < gs["rows"] and _count_sides(gs, y, x) == 4:
            completed.append((y, x))
        # Box above: (y-1, x) — this edge is its bottom
        if y > 0 and _count_sides(gs, y - 1, x) == 4:
            completed.append((y - 1, x))
    else:  # orient == "v"
        # Box right: (y, x) — this edge is its left
        if x < gs["cols"] and _count_sides(gs, y, x) == 4:
            completed.append((y, x))
        # Box left: (y, x-1) — this edge is its right
        if x > 0 and _count_sides(gs, y, x - 1) == 4:
            completed.append((y, x - 1))
    return completed


def apply_move(
    gs: dict, orient: str, y: int, x: int,
) -> dict:
    """Draw an edge and process consequences.

    Returns result dict:
      'completed': list of (by, bx) boxes claimed this turn
      'extra_turn': bool — True if player gets another turn (completed ≥1 box)
      'game_over': bool
    Raises ValueError if edge is illegal.
    """
    if not is_valid_edge(gs, orient, y, x):
        raise ValueError(f"非法边 {orient}{y},{x}")
    player = gs["next"]
    # Draw the edge
    if orient == "h":
        gs["h"][y][x] = player
    else:
        gs["v"][y][x] = player
    # Find newly completed boxes
    completed = _find_completed_boxes(gs, orient, y, x)
    for by, bx in completed:
        gs["boxes"][by][bx] = player
        gs["scores"][player] += 1
    gs["moves"].append({
        "orient": orient, "y": y, "x": x,
        "player": player, "completed": [[by, bx] for by, bx in completed],
    })
    extra_turn = len(completed) > 0
    # Check game over: all edges drawn
    total_edges = (
        (gs["rows"] + 1) * gs["cols"]
        + gs["rows"] * (gs["cols"] + 1)
    )
    drawn_edges = len(gs["moves"])
    game_over = drawn_edges >= total_edges
    if not extra_turn:
        gs["next"] = PLAYER2 if player == PLAYER1 else PLAYER1
    if game_over:
        _resolve_winner(gs)
    return {
        "completed": completed,
        "extra_turn": extra_turn,
        "game_over": game_over,
    }


def _resolve_winner(gs: dict):
    """Set winner/reason based on current scores."""
    s1 = gs["scores"][PLAYER1]
    s2 = gs["scores"][PLAYER2]
    if s1 > s2:
        gs["winner"] = "black"
        gs["reason"] = "score"
    elif s2 > s1:
        gs["winner"] = "white"
        gs["reason"] = "score"
    else:
        gs["winner"] = "draw"
        gs["reason"] = "score"


def is_game_over(gs: dict) -> bool:
    """Check if all edges have been drawn."""
    total_edges = (
        (gs["rows"] + 1) * gs["cols"]
        + gs["rows"] * (gs["cols"] + 1)
    )
    return len(gs["moves"]) >= total_edges


def count_edges(gs: dict) -> int:
    """Return number of drawn edges."""
    return len(gs["moves"])


def total_edges(gs: dict) -> int:
    """Return total number of edges on the board."""
    return (gs["rows"] + 1) * gs["cols"] + gs["rows"] * (gs["cols"] + 1)


def game_to_sync(gs: dict, user_id: int) -> dict:
    """Build the game portion of a sync message for dots."""
    return {
        "dots": gs["dots"],
        "rows": gs["rows"],
        "cols": gs["cols"],
        "h": gs["h"],
        "v": gs["v"],
        "boxes": gs["boxes"],
        "next": gs["next"],
        "scores": gs["scores"],
        "moves": gs["moves"],
        "your_color": _player_to_color(gs, user_id),
        "winner": gs["winner"],
        "reason": gs["reason"],
    }


def _player_to_color(gs: dict, user_id: int) -> str:
    """Map user_id to 'black'/'white' via player_colors in game_state."""
    pc = gs.get("player_colors", {})
    return pc.get(user_id, "")


def snapshot(gs: dict) -> dict:
    """Build final-position snapshot for history records."""
    return {
        "game": "dots",
        "dots": gs["dots"],
        "rows": gs["rows"],
        "cols": gs["cols"],
        "h": gs["h"],
        "v": gs["v"],
        "boxes": gs["boxes"],
        "scores": gs["scores"],
        "winner": gs["winner"],
        "reason": gs["reason"],
    }
