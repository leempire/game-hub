"""Marked-account cleanup preserves real users and mixed history, with backup."""
import json
import sqlite3

import pytest

from app import database
from app.test_user_cleanup import cleanup


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / 'cleanup.db'
    monkeypatch.setattr(database, '_db_path', str(path))
    database.init_db(str(path))
    return path


def add_user(conn, name, batch=None):
    return conn.execute('''INSERT INTO users(username,password_hash,salt,role,created_at,
        temporary_test,test_batch,ai_credit_units) VALUES (?,'h','s','user','now',?,?,123456)''',
        (name, int(batch is not None), batch)).lastrowid


def test_batch_cleanup_preserves_real_users_and_other_batches(db, tmp_path):
    with sqlite3.connect(db) as c:
        real = add_user(c, 'contest_real')
        test = add_user(c, 'normal_name', 'this-task')
        other = add_user(c, 'test_other', 'other-task')
        c.execute('INSERT INTO turtle_soup_seen(user_id,puzzle_key,match_id) VALUES (?, ?, ?)',
                  (test, 'story', 'match'))
        for players in [[test], [test, real]]:
            rid = c.execute('''INSERT INTO game_records(game,room_code,players_json,results_json,
                snapshot_json,started_at,ended_at) VALUES ('gomoku','123456',?,'[]','{}','now','now')''',
                (json.dumps([{'user_id': uid} for uid in players]),)).lastrowid
            for uid in players:
                c.execute("INSERT INTO game_participants(record_id,user_id,result) VALUES (?,?,'draw')",(rid,uid))
    result = cleanup(db, tmp_path / 'backups', 'this-task')
    assert result['deleted_users'] == result['deleted_records'] == 1
    assert sqlite3.connect(result['backup']).execute('SELECT count(*) FROM users WHERE id=?',(test,)).fetchone()[0] == 1
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT id,ai_credit_units FROM users WHERE id IN (?,?) ORDER BY id',(real,other)).fetchall() == [(real,123456),(other,123456)]
        assert c.execute('SELECT count(*) FROM game_records').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM turtle_soup_seen').fetchone()[0] == 0
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []


def test_mixed_room_rolls_back_and_keeps_backup(db,tmp_path):
    with sqlite3.connect(db) as c:
        real=add_user(c,'real_user');test=add_user(c,'temp_user','batch')
        c.execute('CREATE TABLE IF NOT EXISTS rooms(code TEXT PRIMARY KEY,data TEXT NOT NULL)')
        c.execute('INSERT INTO rooms VALUES (?,?)',('123456',json.dumps({'players':[{'id':real},{'id':test}]})))
    with pytest.raises(ValueError,match='真实玩家房间'):
        cleanup(db,tmp_path/'backups')
    assert sqlite3.connect(db).execute('SELECT count(*) FROM users WHERE id=?',(test,)).fetchone()[0] == 1
    assert len(list((tmp_path/'backups').glob('*.db'))) == 1


def test_registration_marker_requires_batch(ws_client,db):
    payload={'username':'marked_user','password':'test123456'}
    assert ws_client.post('/api/auth/register',json={**payload,'temporary_test':True}).status_code == 400
    assert ws_client.post('/api/auth/register',json={**payload,'test_batch':'oops'}).status_code == 400
    assert ws_client.post('/api/auth/register',json={**payload,'temporary_test':True,'test_batch':'task'}).status_code == 200
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT temporary_test,test_batch FROM users WHERE username=?',('marked_user',)).fetchone()==(1,'task')


def test_credit_audit_to_real_user_is_not_erased(db,tmp_path):
    with sqlite3.connect(db) as c:
        real=add_user(c,'real_account');test=add_user(c,'temporary_admin','batch')
        c.execute('INSERT INTO ai_credit_adjustments(admin_id,user_id,previous_units,new_units) VALUES (?,?,0,123456)',(test,real))
    with pytest.raises(ValueError,match='积分'):
        cleanup(db,tmp_path/'backups')
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT count(*) FROM ai_credit_adjustments').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM users WHERE id=?',(test,)).fetchone()[0]==1
