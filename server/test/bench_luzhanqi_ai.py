"""军棋人机的**对局台**（不是 pytest 用例，直接 `python test/bench_luzhanqi_ai.py` 跑）。

功能与用法
----------
回答一个问题：**新版人机到底强了多少**。做法是三种对手各打一批局：

    python test/bench_luzhanqi_ai.py                    # 默认 6 局/档，0.1s/步
    python test/bench_luzhanqi_ai.py --games 12 --ms 250
    python test/bench_luzhanqi_ai.py --mode hidden      # 只跑暗棋

| 对手 | 说明 |
|---|---|
| `random` | 随机走合法着法（最弱的基线） |
| `v1` | **旧版人机**（`test/luzhanqi_ai_v1.py`，v13 之前的实现冻结快照）|

两侧交替执红，红黑各半 —— 军棋先手有优势，只让一方执红会把这个优势算进胜率里。

⚠️ **步时预算对两边相同**（默认 0.1 秒）。旧版是「静态打分 + 一层威胁检查」，
基本不吃时间；新版是迭代加深，**给它更长的预算它会更强** —— 所以这份表量的是
「同样一步 0.1 秒时谁更强」，不是「新版的极限」。要看极限就把 `--ms` 调大。
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import luzhanqi as L                       # noqa: E402
from app import luzhanqi_ai as AI                   # noqa: E402
import luzhanqi_ai_v1 as OLD                        # noqa: E402


def _new_game(mode: str, reveal: bool, seed: int) -> dict:
    rng = random.Random(seed)
    gs = L.create_game(mode, reveal)
    gs["player_colors"] = {1: L.RED, 2: L.BLACK}
    for color in (L.RED, L.BLACK):
        L.fill_layout(gs, color, L.random_layout(color, rng))
    gs["ready"] = {L.RED: False, L.BLACK: False}
    gs["flag_revealed"] = {L.RED: False, L.BLACK: False}
    for color in (L.RED, L.BLACK):
        L.confirm_setup(gs, color)
    return gs


def _mover(kind: str, gs: dict, side: str, budget: float, rng: random.Random):
    if kind == "random":
        return lambda: rng.choice(L.all_moves(gs["board"], side) or [None])
    if kind == "v1":
        return lambda: OLD.best_move(L.view_for(gs, side), time_budget=budget)
    return lambda: AI.best_move(L.view_for(gs, side), time_budget=budget)


def play(red_kind: str, black_kind: str, mode: str, reveal: bool, seed: int,
         budget: float, max_plies: int = 900) -> dict:
    gs = _new_game(mode, reveal, seed)
    rng = random.Random(seed * 31 + 7)
    pick = {"red": _mover(red_kind, gs, L.RED, budget, rng),
            "black": _mover(black_kind, gs, L.BLACK, budget, rng)}
    plies = 0
    while not gs["finished"] and plies < max_plies:
        side = gs["next"]
        move = pick["red" if side == L.RED else "black"]()
        if move is None:
            break
        L.make_move(gs, move[0], move[1])
        plies += 1
    return {"winner": gs.get("winner"), "reason": gs.get("reason"), "plies": plies}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=6, help="每档多少局（红黑各半）")
    ap.add_argument("--ms", type=int, default=100, help="每步的思考预算（毫秒）")
    ap.add_argument("--mode", type=str, default="open,hidden", help="open / hidden")
    ap.add_argument("--opponent", type=str, default="random,v1")
    args = ap.parse_args()

    budget = args.ms / 1000.0
    for mode in [m.strip() for m in args.mode.split(",") if m.strip()]:
        print(f"=== {mode}（每档 {args.games} 局 · 每步 {args.ms}ms）===")
        for opp in [o.strip() for o in args.opponent.split(",") if o.strip()]:
            tally = {"win": 0, "loss": 0, "draw": 0, "unfinished": 0}
            t0 = time.monotonic()
            for i in range(args.games):
                new_is_red = i % 2 == 0
                red, black = ("new", opp) if new_is_red else (opp, "new")
                out = play(red, black, mode, True, 1000 + i, budget)
                w = out["winner"]
                if w is None:
                    tally["unfinished"] += 1
                elif w == "draw":
                    tally["draw"] += 1
                elif (w == L.RED) == new_is_red:
                    tally["win"] += 1
                else:
                    tally["loss"] += 1
            dt = time.monotonic() - t0
            print(f"  新版 vs {opp:7s}：胜 {tally['win']} / 负 {tally['loss']} "
                  f"/ 和 {tally['draw']} / 未终局 {tally['unfinished']}"
                  f"（{dt:.0f}s）")
        sys.stdout.flush()


if __name__ == "__main__":
    main()