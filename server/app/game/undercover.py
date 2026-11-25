"""Pure rules and player-specific views for the word deduction game."""

from collections import Counter
from copy import deepcopy
import unicodedata


MIN_PLAYERS = 4
MAX_PLAYERS = 8
MAX_ROUNDS = 8
CLUE_MS = 45_000
VOTE_MS = 30_000
MAX_CLUE_LENGTH = 60

WORD_PAIRS = (
    ("雨伞", "雨衣"), ("豆浆", "燕麦奶"), ("馒头", "花卷"), ("汤圆", "年糕"),
    ("馄饨", "饺子"), ("凉面", "凉皮"), ("酸奶", "布丁"), ("薯片", "锅巴"),
    ("咖啡", "奶茶"), ("奶酪", "黄油"), ("烤鸭", "烧鹅"), ("酱油", "香醋"),
    ("荔枝", "龙眼"), ("柚子", "橙子"), ("桃子", "李子"), ("红薯", "山药"),
    ("河马", "犀牛"), ("蜻蜓", "蝴蝶"), ("海鸥", "信天翁"), ("松鼠", "仓鼠"),
    ("海豹", "海狮"), ("青蛙", "蟾蜍"), ("鸽子", "麻雀"), ("斑马", "长颈鹿"),
    ("竹林", "松林"), ("沙漠", "戈壁"), ("池塘", "水库"), ("雾气", "炊烟"),
    ("冰雹", "雪花"), ("朝霞", "夕阳"), ("帐篷", "睡袋"), ("滑板", "轮滑"),
    ("阳台", "露台"), ("书店", "图书馆"), ("车站", "码头"), ("胡同", "步行街"),
    ("信封", "明信片"), ("签字笔", "钢笔"), ("尺子", "卷尺"), ("日历", "钟表"),
    ("砧板", "擀面杖"), ("竹筷", "木勺"), ("暖瓶", "马克杯"), ("冰箱", "保温箱"),
    ("书包", "行李箱"), ("窗帘", "屏风"), ("眼镜", "放大镜"), ("蒲扇", "折扇"),
    ("冰鞋", "雪橇"), ("口琴", "笛子"), ("皮影", "木偶"), ("风筝", "气球"),
    ("跑步", "竞走"), ("划船", "游泳"), ("排球", "篮球"), ("台球", "保龄球"),
    ("风铃", "门铃"), ("口罩", "围巾"), ("拼图", "积木"), ("篮子", "纸箱"),
)


def choose_word_pair(rng, previous=None):
    if previous is None:
        return rng.choice(WORD_PAIRS)
    excluded = frozenset(previous)
    return rng.choice([pair for pair in WORD_PAIRS if frozenset(pair) != excluded])


def create_game(player_ids, word_pair, match_id, rng):
    players = list(player_ids)
    if not MIN_PLAYERS <= len(players) <= MAX_PLAYERS or len(set(players)) != len(players):
        raise ValueError("我是卧底需要 4–8 名不同玩家")
    words = list(word_pair)
    rng.shuffle(words)
    shuffled_players = players.copy()
    rng.shuffle(shuffled_players)
    spies = set(shuffled_players[:2 if len(players) >= 7 else 1])
    gs = {
        "match_id": match_id,
        "revision": 0,
        "phase_id": 0,
        "round": 1,
        "start_index": rng.randrange(len(players)),
        "player_ids": players,
        "alive": players.copy(),
        "departed": [],
        "roles": {uid: "undercover" if uid in spies else "civilian" for uid in players},
        "word_pair": {"civilian": words[0], "undercover": words[1]},
        "clues": [],
        "votes": {},
        "tallies": [],
        "eliminations": [],
        "winner": None,
        "reason": None,
    }
    _begin_clues(gs)
    return gs


def _open_phase(gs, phase, duration):
    gs["phase"] = phase
    gs["phase_id"] += 1
    gs["remaining_ms"] = duration


def _begin_clues(gs):
    players = gs["player_ids"]
    start = gs["start_index"]
    order = [uid for uid in players[start:] + players[:start] if uid in gs["alive"]]
    gs["start_index"] = players.index(order[0])
    gs["turn_order"] = order
    gs["pending_speakers"] = order.copy()
    _open_phase(gs, "clue", CLUE_MS)


def _begin_vote(gs, candidates, *, runoff=False):
    gs["vote_targets"] = list(candidates)
    gs["votes"] = {}
    _open_phase(gs, "runoff" if runoff else "vote", VOTE_MS)


def _finish(gs, winner, reason):
    gs["winner"] = winner
    gs["reason"] = reason
    gs["clues"].clear()
    gs["votes"].clear()
    _open_phase(gs, "finished", 0)


def _check_win(gs):
    alive = gs["alive"]
    spies = sum(gs["roles"][uid] == "undercover" for uid in alive)
    if not alive:
        _finish(gs, "aborted", "abandoned")
    elif spies == 0:
        _finish(gs, "civilian", "no_undercover")
    elif spies * 2 >= len(alive):
        _finish(gs, "undercover", "parity")
    return gs["winner"] is not None


def _record_elimination(gs, uid, reason):
    gs["alive"].remove(uid)
    gs["eliminations"].append({"round": gs["round"], "user_id": uid, "role": gs["roles"][uid], "reason": reason})


def _next_clue(gs, text):
    uid = gs["pending_speakers"].pop(0)
    gs["clues"].append({"round": gs["round"], "user_id": uid, "text": text})
    if gs["pending_speakers"]:
        _open_phase(gs, "clue", CLUE_MS)
    else:
        _begin_vote(gs, gs["alive"])


def _valid_stage(gs, phase_id, phases):
    return type(phase_id) is int and phase_id == gs["phase_id"] and gs["phase"] in phases


def _normalize_text(text):
    return unicodedata.normalize("NFKC", text).casefold()


def apply_clue(gs, uid, text, phase_id):
    if not _valid_stage(gs, phase_id, ("clue",)):
        return "这次发言已结束，请查看最新回合"
    if gs["pending_speakers"][:1] != [uid]:
        return "还没轮到你发言"
    if not isinstance(text, str) or len(text) > MAX_CLUE_LENGTH or not text.strip() or any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in text
    ):
        return "请输入一行 1–60 字的线索"
    text = text.strip()
    if _normalize_text(gs["word_pair"][gs["roles"][uid]]) in _normalize_text(text):
        return "线索不能直接写出你的词语"
    gs["revision"] += 1
    _next_clue(gs, text)
    return None


def _candidates(gs, uid):
    if gs["phase"] not in ("vote", "runoff") or uid not in gs["alive"]:
        return []
    return [target for target in gs["vote_targets"] if target != uid]


def _all_voted(gs):
    return all(uid in gs["votes"] or not _candidates(gs, uid) for uid in gs["alive"])


def _close_vote(gs):
    runoff = gs["phase"] == "runoff"
    counts = Counter(gs["votes"].values())
    votes = [{"user_id": uid, "count": counts[uid]} for uid in gs["vote_targets"]]
    gs["tallies"].append({"round": gs["round"], "runoff": runoff, "votes": votes})
    highest = max(counts.values(), default=0)
    leaders = [uid for uid in gs["vote_targets"] if highest and counts[uid] == highest]
    if len(leaders) > 1 and not runoff:
        _begin_vote(gs, leaders, runoff=True)
        return
    gs["votes"].clear()
    if len(leaders) == 1:
        _record_elimination(gs, leaders[0], "vote")
    if _check_win(gs):
        return
    if gs["round"] == MAX_ROUNDS:
        _finish(gs, "draw", "round_limit")
        return
    gs["round"] += 1
    gs["start_index"] = (gs["start_index"] + 1) % len(gs["player_ids"])
    _begin_clues(gs)


def apply_vote(gs, uid, target_id, phase_id):
    if not _valid_stage(gs, phase_id, ("vote", "runoff")):
        return "这次投票已结束，请查看最新回合"
    if type(target_id) is not int or target_id not in _candidates(gs, uid):
        return "请选择一名可投票的其他玩家"
    if uid in gs["votes"]:
        return None if gs["votes"][uid] == target_id else "你已确认本次投票"
    gs["votes"][uid] = target_id
    gs["revision"] += 1
    if _all_voted(gs):
        _close_vote(gs)
    return None


def expire(gs):
    if gs["winner"]:
        return False
    gs["revision"] += 1
    if gs["phase"] == "clue":
        if gs["pending_speakers"]:
            _next_clue(gs, None)
        else:
            _resolve_departures(gs)
    else:
        _close_vote(gs)
    return True


def _resolve_departures(gs):
    if _check_win(gs):
        return
    if gs["phase"] == "clue":
        if not gs["pending_speakers"]:
            _begin_vote(gs, gs["alive"])
    elif _all_voted(gs):
        _close_vote(gs)


def depart(gs, uid, reason, *, settle=True):
    if gs["winner"] or uid not in gs["alive"]:
        return False
    _record_elimination(gs, uid, reason)
    gs["departed"].append(uid)
    gs["revision"] += 1
    if gs["phase"] == "clue":
        pending = gs["pending_speakers"]
        was_speaker = pending[:1] == [uid]
        if uid in pending:
            pending.remove(uid)
        if was_speaker and pending:
            _open_phase(gs, "clue", CLUE_MS)
    else:
        if uid in gs["vote_targets"]:
            gs["vote_targets"].remove(uid)
        gs["votes"] = {voter: target for voter, target in gs["votes"].items() if uid not in (voter, target)}
    if settle:
        _resolve_departures(gs)
    return True


def resolve_departures(gs):
    if gs["winner"]:
        return False
    phase = gs["phase_id"]
    _resolve_departures(gs)
    if gs["phase_id"] == phase:
        return False
    gs["revision"] += 1
    return True


def view_for(gs, uid):
    playing_clues = gs["phase"] == "clue" and gs["pending_speakers"]
    speaker = gs["pending_speakers"][0] if playing_clues else None
    published = {clue["user_id"] for clue in gs["clues"] if clue["round"] == gs["round"]}
    return {
        "match_id": gs["match_id"],
        "revision": gs["revision"],
        "phase_id": gs["phase_id"],
        "phase": gs["phase"],
        "round": gs["round"],
        "remaining_ms": gs["remaining_ms"],
        "speaker_id": speaker,
        "player_ids": gs["player_ids"].copy(),
        "alive": gs["alive"].copy(),
        "turn_order": [player for player in gs["turn_order"] if player in gs["alive"] or player in published],
        "clues": deepcopy(gs["clues"]),
        "eliminations": deepcopy(gs["eliminations"]),
        "tallies": deepcopy(gs["tallies"]),
        "candidate_ids": _candidates(gs, uid),
        "votes_cast": len(gs["votes"]),
        "your_vote": gs["votes"].get(uid),
        "your_word": gs["word_pair"].get(gs["roles"].get(uid)),
        "can_clue": speaker is not None and speaker == uid,
    }


def snapshot(gs):
    if not gs["winner"]:
        return None
    return {
        "game": "undercover",
        "round": gs["round"],
        "winner": gs["winner"],
        "reason": gs["reason"],
        "roles": [{"user_id": uid, "role": gs["roles"][uid]} for uid in gs["player_ids"]],
        "word_pair": gs["word_pair"].copy(),
        "eliminations": deepcopy(gs["eliminations"]),
        "tallies": deepcopy(gs["tallies"]),
        "forfeited": gs["departed"].copy(),
    }
