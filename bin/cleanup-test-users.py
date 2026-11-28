#!/usr/bin/env python3
"""Preview or delete marked test players, with backup and managed backend stop/start.

Usage: python3 bin/cleanup-test-users.py --batch <task-id> [--apply]
       python3 bin/cleanup-test-users.py --apply  # all marked batches
       python3 bin/cleanup-test-users.py --db /tmp/test.db --apply  # isolated DB
"""

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'server'))
from app.cfg import config
from app.test_user_cleanup import cleanup, selected_users


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--db', type=Path, help='isolated database; skips service management')
    args = parser.parse_args()
    configured = Path(config.get('db_path', 'data/gamehub.db'))
    live_db = configured if configured.is_absolute() else ROOT / 'server' / configured
    path = (args.db or live_db).resolve()
    live = path == live_db.resolve()
    if not path.is_file():
        parser.error('数据库不存在')
    if not args.apply:
        with sqlite3.connect(path) as conn:
            rows = selected_users(conn, args.batch)
        print(json.dumps({'mode': 'preview', 'users': len(rows), 'sample': rows[:10]}, ensure_ascii=False))
        return
    running = live and subprocess.run([str(ROOT / 'scripts/status.sh'), 'backend'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if running:
        subprocess.run([str(ROOT / 'scripts/stop.sh'), 'backend'], check=True)
    try:
        result = cleanup(path, ROOT / 'tmp/backups', args.batch)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        if running:
            subprocess.run([str(ROOT / 'scripts/start.sh'), 'backend'], check=True)


if __name__ == '__main__':
    main()
