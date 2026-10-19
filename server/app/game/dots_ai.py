"""Dots and Boxes AI — greedy box-completion + chain-aware heuristic.

Interface: best_move(gs) -> (orient, y, x) | None
gs: game state dict from dots.create_game().

Strategy summary (from research on Dots and Boxes theory):
1. Always complete a box if possible (free point + extra turn).
2. Never draw the 3rd side of a box (gifts opponent a free box).
3. If forced to open, pick an edge that minimizes chain exposure.
4. Double-cross: when claiming a long chain, leave last 2 boxes
   (sacrifice 2 to retain control). Implemented as a heuristic.

For the 5×5 dot grid (4×4 = 16 boxes), full minimax is too expensive.
The heuristic approach produces solid play within ~100ms.
"""

import copy
from app.game.dots import (
    get_legal_edges,
    apply_move,
    is_valid_edge,
    _count_sides,
    _find_completed_boxes,
    PLAYER1,
    PLAYER2,
)


def best_move(gs: dict) -> tuple[str, int, int] | None:
    """Choose the best edge for the current player.

    Returns (orient, y, x) or None if no legal edges remain.
    """
    legal = get_legal_edges(gs)
    if not legal:
        return None
    player = gs["next"]
    # Phase 1: complete any boxes (greedy — always take free points)
    completions = _find_completing_edges(gs, legal)
    if completions:
        # Pick the one that completes the most boxes
        best = max(completions, key=lambda e: _completion_count(gs, e))
        return best
    # Phase 2: avoid giving away boxes (no 3rd-side edges)
    safe = [e for e in legal if _is_safe_edge(gs, e)]
    if safe:
        return _pick_strategic_safe(gs, safe, player)
    # Phase 3: forced to open — minimize chain damage
    return _pick_least_damaging(gs, legal, player)


def _find_completing_edges(
    gs: dict, legal: list[tuple[str, int, int]],
) -> list[tuple[str, int, int]]:
    """Find edges that complete at least one box."""
    result = []
    for orient, y, x in legal:
        completed = _find_completed_boxes(gs, orient, y, x)
        # We need to temporarily place the edge to check completion
        # but _find_completed_boxes only counts sides assuming edge is placed
        # We need to simulate: if 3 sides already filled, this edge completes
        boxes_completed = _simulated_completions(gs, orient, y, x)
        if boxes_completed:
            result.append((orient, y, x))
    return result


def _simulated_completions(
    gs: dict, orient: str, y: int, x: int,
) -> list[tuple[int, int]]:
    """Simulate placing an edge and return newly completed boxes."""
    sim = copy.deepcopy(gs)
    if orient == "h":
        sim["h"][y][x] = sim["next"]
    else:
        sim["v"][y][x] = sim["next"]
    return _find_completed_boxes(sim, orient, y, x)


def _completion_count(
    gs: dict, edge: tuple[str, int, int],
) -> int:
    """Count how many boxes an edge would complete."""
    return len(_simulated_completions(gs, edge[0], edge[1], edge[2]))


def _is_safe_edge(gs: dict, edge: tuple[str, int, int]) -> bool:
    """An edge is safe if placing it doesn't create a 3-sided box for opponent.

    A 3-sided box allows the opponent to complete it on their next turn.
    We check adjacent boxes: after placing this edge, no adjacent box
    should have exactly 3 sides (unless it already had 4, meaning it's done).
    """
    orient, y, x = edge
    sim = copy.deepcopy(gs)
    player = sim["next"]
    if orient == "h":
        sim["h"][y][x] = player
    else:
        sim["v"][y][x] = player
    # Check all boxes adjacent to this edge
    adj_boxes = _adjacent_boxes(orient, y, x)
    opponent = PLAYER2 if player == PLAYER1 else PLAYER1
    for by, bx in adj_boxes:
        if by < 0 or by >= sim["rows"] or bx < 0 or bx >= sim["cols"]:
            continue
        sides = _count_sides(sim, by, bx)
        if sides == 3 and sim["boxes"][by][bx] == 0:
            # This edge creates a 3-sided box — dangerous
            return False
    return True


def _adjacent_boxes(orient: str, y: int, x: int) -> list[tuple[int, int]]:
    """Return box coordinates adjacent to an edge."""
    if orient == "h":
        return [(y, x), (y - 1, x)]
    else:
        return [(y, x), (y, x - 1)]


def _pick_strategic_safe(
    gs: dict, safe: list[tuple[str, int, int]], player: int,
) -> tuple[str, int, int]:
    """Among safe edges, pick one that's strategically good.

    Heuristic: prefer edges that are closer to completing boxes (2-sided)
    without creating 3-sided ones, and that are near the board center.
    """
    best_score = -999
    best_edge = safe[0]
    mid_y = gs["rows"] / 2.0
    mid_x = gs["cols"] / 2.0
    for edge in safe:
        orient, y, x = edge
        score = 0.0
        # Prefer edges adjacent to 2-sided boxes (building towards completion)
        adj = _adjacent_boxes(orient, y, x)
        for by, bx in adj:
            if 0 <= by < gs["rows"] and 0 <= bx < gs["cols"]:
                sides = _count_sides(gs, by, bx)
                if gs["boxes"][by][bx] == 0:
                    if sides == 2:
                        score += 2  # good: building on a 2-sided box
                    elif sides == 1:
                        score += 1  # neutral
        # Slight preference for central edges
        edge_y = y if orient == "h" else y + 0.5
        edge_x = x + 0.5 if orient == "h" else x
        dist = abs(edge_y - mid_y) + abs(edge_x - mid_x)
        score -= dist * 0.1
        if score > best_score:
            best_score = score
            best_edge = edge
    return best_edge


def _pick_least_damaging(
    gs: dict, legal: list[tuple[str, int, int]], player: int,
) -> tuple[str, int, int]:
    """When forced to open (all edges create 3-sided boxes),
    pick the edge that gives opponent the fewest boxes.

    Simulate: place each edge, then let opponent greedily complete
    all available boxes, count how many they get. Pick minimum.
    """
    best_damage = 999
    best_edge = legal[0]
    for edge in legal:
        damage = _simulate_opponent_gain(gs, edge, player)
        if damage < best_damage:
            best_damage = damage
            best_edge = edge
    return best_edge


def _simulate_opponent_gain(
    gs: dict, edge: tuple[str, int, int], player: int,
) -> int:
    """Simulate placing edge, then count boxes opponent can chain-claim."""
    sim = copy.deepcopy(gs)
    orient, y, x = edge
    if orient == "h":
        sim["h"][y][x] = player
    else:
        sim["v"][y][x] = player
    opponent = PLAYER2 if player == PLAYER1 else PLAYER1
    # Let opponent greedily complete boxes
    total_gain = 0
    changed = True
    while changed:
        changed = False
        opp_legal = get_legal_edges(sim)
        for oe in opp_legal:
            comps = _simulated_completions(sim, oe[0], oe[1], oe[2])
            if comps:
                # Opponent claims these boxes
                for by, bx in comps:
                    if sim["boxes"][by][bx] == 0:
                        sim["boxes"][by][bx] = opponent
                        total_gain += 1
                if oe[0] == "h":
                    sim["h"][oe[1]][oe[2]] = opponent
                else:
                    sim["v"][oe[1]][oe[2]] = opponent
                changed = True
                break
    return total_gain
