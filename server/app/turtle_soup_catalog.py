"""Server-only public puzzle batches and transactional all-player exclusion."""

from functools import lru_cache
import json
from pathlib import Path
import random

from app.database import get_conn

CATALOG_DIR = Path(__file__).resolve().parent.parent / 'content' / 'turtle_soup'


@lru_cache(maxsize=1)
def all_puzzles():
    puzzles = []
    for path in sorted(CATALOG_DIR.glob('*.json')):
        puzzles.extend(json.loads(path.read_text(encoding='utf-8'))['puzzles'])
    ids = set()
    for puzzle in puzzles:
        if puzzle['id'] in ids or not all(puzzle.get(key) for key in (
            'id', 'title', 'surface', 'solution', 'essential_facts', 'source', 'family',
        )):
            raise ValueError('海龟汤题库条目无效或题号重复')
        ids.add(puzzle['id'])
        hints = puzzle.get('hints')
        if (not isinstance(hints, list) or len(hints) != 3
                or any(not isinstance(hint, str) or not hint.strip() for hint in hints)):
            raise ValueError('海龟汤需要三档渐进提示')
    if not puzzles:
        raise ValueError('海龟汤题库为空')
    return tuple(puzzles)


def draw_for_players(player_ids, match_id):
    puzzles = all_puzzles()
    conn = get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        marks = ','.join('?' for _ in player_ids)
        seen = {row[0] for row in conn.execute(
            f'SELECT puzzle_key FROM turtle_soup_seen WHERE user_id IN ({marks})', tuple(player_ids))}
        choices = [puzzle for puzzle in puzzles if puzzle['family'] not in seen]
        if not choices:
            raise ValueError('房内玩家已玩过全部可选汤题，请更换参与者或等待题库扩充')
        puzzle = random.SystemRandom().choice(choices)
        conn.executemany('''INSERT INTO turtle_soup_seen(user_id,puzzle_key,match_id) VALUES (?,?,?)''',
                         [(uid, puzzle['family'], match_id) for uid in player_ids])
        conn.commit()
        return puzzle
    finally:
        conn.close()
