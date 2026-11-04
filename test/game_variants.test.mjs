import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import { snapshotAtStep } from '../app/src/replay.ts';
import { mountGame } from './game_screen_harness.mjs';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const ts = require('typescript');
const theme = require('../app/src/theme.ts');
const blank = (rows, cols = rows) => Array.from({ length: rows }, () => Array(cols).fill(0));
const state = (size, extra = {}) => ({ board_size: size, board: blank(size), moves: [], next: 'black', your_color: 'black', winner: null, reason: null, legal: [], ...extra });

for (const os of ['android', 'web']) {
  test(`${os}: 19路五子棋和6/10路翻转棋按权威尺寸渲染`, () => {
    const gomoku = mountGame('Gomoku', state(19), { os, game: 'gomoku' });
    assert.equal(gomoku.count('gomoku-cell-'), 361);
    for (const size of [6, 10]) {
      const reversi = mountGame('Reversi', state(size, { legal: [[size - 1, size - 1]], score: { black: 0, white: 0 } }), { os, game: 'reversi' });
      assert.equal(reversi.count('cell-'), size * size);
      assert.equal(reversi.get('cell-0-0').disabled, true);
      assert.equal(reversi.get(`cell-${size - 1}-${size - 1}`).disabled, false);
      reversi.press(`cell-${size - 1}-${size - 1}`);
      assert.equal(JSON.stringify(reversi.calls), JSON.stringify([['move', size - 1, size - 1]]));
    }
  });
  test(`${os}: PopOut只显示权威动作并采用服务端完整board`, () => {
    const board = blank(6, 7); board[5][0] = 1; board[4][0] = 2;
    const view = mountGame('ConnectFour', { ...state(7), rows: 6, cols: 7, board, popout: true, legal_actions: [{ action: 'pop', col: 0 }] }, { os, game: 'connect4' });
    assert.equal(view.get('c4-pop-0').disabled, false);
    assert.equal(view.get('c4-pop-1').disabled, true);
    view.press('c4-pop-0');
    assert.equal(JSON.stringify(view.calls), JSON.stringify([['drop', 0, 'pop']]));
    view.receive('move', { action: 'pop', x: 0, y: 5, color: 'black', next: 'white', board: blank(6, 7), legal_actions: [] });
    assert.equal(view.count('c4-disc-'), 0);
  });
  test(`${os}: 象棋悔棋由can_undo控制，完整xiangqi_state刷新棋盘和记谱`, () => {
    const board = Array.from({ length: 10 }, () => Array(9).fill(null)); board[9][4] = 'K'; board[0][4] = 'k';
    const room = { game: 'xiangqi', host_id: 1, players: [{ id: 1, username: 'human' }, { id: 2, username: 'bot', is_bot: true }], settings: [{ key: 'allow_undo', value: true }] };
    const game = { rows: 10, cols: 9, board, moves: [{ from: [7, 1], to: [4, 1], piece: 'C', captured: null, notation: '炮八进三' }], next: 'red', your_color: 'red', winner: null, reason: null, check: false, last_move: null, legal: [], can_undo: true };
    const view = mountGame('Xiangqi', game, { os, game: 'xiangqi', room });
    assert.match(view.render(), /炮八进三/);
    view.press('xiangqi-undo'); view.press('xiangqi-undo');
    assert.equal(JSON.stringify(view.calls), JSON.stringify([['undo']]));
    const html = view.receive('xiangqi_state', { game: { ...game, moves: [], can_undo: false, revision: 2 } });
    assert.ok(!html.includes('炮八进三'));
    assert.equal(view.get('xiangqi-undo').disabled, true);
  });
}

test('PopOut回放逐手移底重排，且动态翻转棋从对应中央四子起步', () => {
  const snap = { game: 'connect4', rows: 6, cols: 7, moves: [
    { x: 0, y: 5, color: 'black' }, { x: 0, y: 4, color: 'white' }, { x: 0, y: 5, color: 'black', action: 'pop' },
  ] };
  assert.equal(snapshotAtStep(snap, 2).board[5][0], 1);
  const popped = snapshotAtStep(snap, 3).board;
  assert.equal(popped[5][0], 2); assert.equal(popped[4][0], 0);
  for (const size of [6, 10]) {
    const initial = snapshotAtStep({ game: 'reversi', board_size: size, moves: [] }, 0).board;
    assert.equal(initial.flat().filter(Boolean).length, 4);
    assert.equal(initial[size / 2][size / 2], 2);
  }
});

test('棋钟只绘制权威剩余值和单段读秒，暂停不扣时且不推断胜负', () => {
  const source = ts.transpileModule(readFileSync(new URL('../app/src/GameClock.tsx', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  const exports = {};
  runInNewContext(source, { exports, require: name => name === 'react' ? {} : name === 'react-native' ? { StyleSheet: { create: value => value } } : theme });
  const clock = { remaining_ms: { red: 1000, black: 0 }, turn: 'red', period_ms: 10000, period_left_ms: 10000, expired: null, paused: false, receivedAt: 100 };
  const values = exports.clockValues(clock, 2600);
  assert.equal(values[0].milliseconds, 8500); assert.equal(values[0].inPeriod, true);
  assert.equal(values[1].milliseconds, 10000);
  assert.equal(exports.clockValues({ ...clock, paused: true }, 2600)[0].milliseconds, 1000);
  assert.equal(exports.clockValues({ ...clock, remaining_ms: { red: 0 }, period_left_ms: 3500, paused: true }, 2600)[0].milliseconds, 3500);
  assert.equal(exports.clockValues({ ...clock, remaining_ms: { red: 0 }, period_left_ms: 0, expired: 'red' }, 2600)[0].milliseconds, 0);
  assert.equal(clock.expired, null);
});

test('军棋布阵剩余值与终局真相由服务端提供，客户端不提前揭牌', () => {
  const fixture = JSON.parse(execFileSync('python', ['-c', `
import json
from app.game import luzhanqi
s=luzhanqi.create_game(mode='hidden');s['player_colors']={1:'red',2:'black'}
view=luzhanqi.game_to_sync(s,1);view['setup_remaining_ms']=30000
print(json.dumps(view))
`], { cwd: fileURLToPath(new URL('../server', import.meta.url)), encoding: 'utf8' }));
  const view = mountGame('Luzhanqi', fixture, { game: 'luzhanqi' });
  assert.ok(view.get('luzhanqi-setup-countdown'));
  assert.ok(!view.get('lz-truth-board'));
  view.receive('setup_left', { remaining_ms: 10000 });
  view.receive('game_over', { winner: 'red', reason: 'surrender' });
  view.receive('luzhanqi_state', { game: { ...fixture, phase: 'playing', truth_available: true, winner: 'red' } });
  assert.ok(view.get('lz-truth-board'));
});

for (const os of ['android', 'web']) {
  test(`${os}: UNO质疑与抓漏喊完全按逐人can字段显示，累计分区分单局与整赛`, () => {
    const fixture = JSON.parse(execFileSync('python', ['-c', `
import json
from app.game import uno
s=uno.create_game([1,2]);print(json.dumps(uno.game_to_sync(s,1)))
`], { cwd: fileURLToPath(new URL('../server', import.meta.url)), encoding: 'utf8' }));
    const view = mountGame('Uno', { ...fixture, can_challenge_wild4: true, catch_uno_targets: [1] }, { os, game: 'uno' });
    view.press('uno-challenge-wild4'); view.press('uno-catch-1');
    assert.equal(JSON.stringify(view.calls), JSON.stringify([['challenge'], ['catch', 1]]));
    view.receive('uno_state', { game: { ...fixture, can_challenge_wild4: false, catch_uno_targets: [] } });
    assert.ok(!view.get('uno-challenge-wild4')); assert.ok(!view.get('uno-catch-1'));
    const html = view.receive('game_over', { winner: 0, reason: 'emptied', ranking: [], series: {
      target: 500, scores: { 1: 150, 2: 0 }, user_ids: [1, 2], rounds: 1, finished: false, winners: [],
    } });
    assert.match(html, /本局结束，系列赛继续/); assert.match(html, /150 分/);
  });
}

test('19路五子棋触摸命中最末格，禁手合法清单拒绝其他空格', () => {
  const view = mountGame('Gomoku', state(19, { forbidden: true, legal: [[18, 18]] }), { game: 'gomoku' });
  function tap(x, y) {
    const cell = view.get('gomoku-cell-18-18').style.at(-1).width;
    const event = { nativeEvent: { locationX: (x + 0.5) * cell, locationY: (y + 0.5) * cell, touches: [] } };
    view.handlers[0].onPanResponderGrant(event); view.handlers[0].onPanResponderRelease(event); view.render();
  }
  tap(0, 0); tap(0, 0);
  assert.equal(view.calls.length, 0); assert.ok(view.notices.length > 0);
  tap(18, 18); tap(18, 18);
  assert.equal(JSON.stringify(view.calls), JSON.stringify([['move', 18, 18]]));
});

test('飞行棋队伍身份和胜队来自payload，动画时长采用animation_scale', () => {
  const fixture = JSON.parse(execFileSync('python', ['-c', `
import json
from app.game import ludo
s=ludo.create_game(4,teams='2v2',animation_speed='fast');print(json.dumps(ludo.game_to_sync(s)))
`], { cwd: fileURLToPath(new URL('../server', import.meta.url)), encoding: 'utf8' }));
  fixture.your_seat = 0;
  const room = { game: 'ludo', host_id: 1, players: Array.from({ length: 4 }, (_, seat) => ({ id: seat + 1, username: `player${seat + 1}` })) };
  const view = mountGame('Ludo', fixture, { game: 'ludo', room });
  assert.match(view.render(), /第 1 队/); assert.match(view.render(), /第 2 队/);
  view.animations.length = 0;
  view.receive('piece_moved', { seat: 0, piece_id: 0, old_progress: -1, new_progress: 0, dice: 6,
    path: [0], segments: [{ from: -1, to: 0, kind: 'walk' }], captures: [], current_seat: 1, finished: false });
  assert.ok(view.animations.some(config => config.duration === 115 * fixture.animation_scale));
  const html = view.receive('game_over', { winner_team: 1, ranking: [], reason: 'team_finished' });
  assert.match(html, /第 2 队获胜/);
});
