// Shared chrome contract: identities/turns, reconnect visibility and result navigation on both platforms.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');
const theme = require('../app/src/theme.ts');

function harness(os) {
  const controls = new Map(), guards = [];
  const host = name => props => {
    if (props.testID) controls.set(props.testID, props);
    if (name === 'modal') controls.set('modal', props);
    return React.createElement(name === 'input' ? 'input' : 'div', {
      'data-testid': props.testID, 'aria-label': props.accessibilityLabel,
    }, name === 'input' ? undefined : props.children);
  };
  const modules = {
    react: React,
    'react-native': { View: host('view'), Text: host('text'), TouchableOpacity: host('button'),
      Modal: props => props.visible ? host('modal')(props) : null, TextInput: host('input'),
      ScrollView: host('scroll'), KeyboardAvoidingView: host('keyboard'), Platform: { OS: os },
      StyleSheet: { create: value => value } },
    './theme': theme, './backGuard': { useModalBackGuard: (...args) => guards.push(args) },
    './help': { GameHelpButton: () => null }, './games': { gameTitle: key => `游戏 ${key}` },
  };
  const load = path => {
    const exports = {};
    const source = ts.transpileModule(readFileSync(new URL(`../app/src/${path}`, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
    }).outputText;
    runInNewContext(source, { exports, require: name => {
      assert.ok(name in modules, `Unexpected import ${name}`); return modules[name];
    } });
    return exports;
  };
  modules['./ui'] = load('ui.tsx');
  modules['./GameClock'] = load('GameClock.tsx');
  const shell = load('GameShell.tsx'), players = load('gamePlayers.ts');
  return { ...shell, ...players, ...modules['./ui'], controls, guards,
    render: element => renderToStaticMarkup(element) };
}

for (const os of ['android', 'web']) {
  test(`${os}: reconnect and both identities remain visible`, () => {
    const h = harness(os);
    const html = h.render(React.createElement(h.default, { game: 'gomoku', code: '123456', connected: false,
      players: [{ id: 1, name: '张三', identity: '黑棋', isMe: true, active: true },
        { id: 2, name: '李四', identity: '白棋', isBot: true }], children: '棋盘' }));
    for (const label of ['张三', '李四', '黑棋', '白棋', '行动中', '重连中', '123456', '棋盘']) assert.ok(html.includes(label));
  });
  test(`${os}: result buttons and native/browser back use the supplied return callback`, () => {
    const h = harness(os), calls = [];
    const back = () => calls.push('room'), history = () => calls.push('history');
    h.render(React.createElement(h.GameResult, { visible: true, result: '黑方胜', onBackToRoom: back, onViewHistory: history }));
    h.controls.get('game-result-history').onPress();
    h.controls.get('game-result-room').onPress();
    h.controls.get('modal').onRequestClose();
    assert.equal(h.guards[0][0], true);
    h.guards[0][1]();
    assert.deepEqual(calls, ['history', 'room', 'room', 'room']);
  });
}

test('public identities follow the recipient color after swapping seats', () => {
  const h = harness('web');
  const room = { game: 'xiangqi', players: [{ id: 7, nickname: '甲' }, { id: 8, nickname: '乙' }] };
  const players = h.shellPlayers(room, 8, { your_color: 'red', next: 'black' });
  assert.equal(players[0].identity, '黑方'); assert.equal(players[0].active, true);
  assert.equal(players[1].identity, '红方'); assert.equal(players[1].isMe, true);
  assert.equal(h.shellPlayers(room, 8, { your_color: 'red', next: 'black', winner: 'red' }).some(p => p.active), false);
});
test('ludo uses payload colors and dots translates numeric turn without inventing rules', () => {
  const h = harness('web');
  const players = [{ id: 1, username: 'a' }, { id: 2, username: 'b' }];
  const ludo = h.shellPlayers({ game: 'ludo', players }, 1, { current_seat: 1, players: { 0: { color: 'blue' }, 1: { color: 'green' } } });
  assert.equal(ludo[1].identity, '绿方'); assert.equal(ludo[1].active, true);
  const dots = h.shellPlayers({ game: 'dots', players }, 1, { your_color: 'white', next: 1 });
  assert.equal(dots[0].identity, '红方'); assert.equal(dots[1].identity, '蓝方'); assert.equal(dots[1].active, true);
});

test('battleship blue/red identities and turn are correct from both recipients', () => {
  const h = harness('web');
  const room = { game: 'battleship', players: [{ id: 1, username: 'blue-player' }, { id: 2, username: 'red-player' }] };
  for (const [userId, mine] of [[1, 'blue'], [2, 'red']]) {
    const players = h.shellPlayers(room, userId, { your_color: mine, next: 'blue' });
    assert.equal(players[0].identity, '蓝方'); assert.equal(players[1].identity, '红方');
    assert.equal(players[0].active, true); assert.equal(players[1].active, false);
  }
});
test('UNO winner seat zero ends highlight; payload seats survive lobby member removal', () => {
  const h = harness('web');
  const room = { game: 'uno', players: [{ id: 11, username: 'left' }, { id: 33, username: 'right' }] };
  const state = { seat_uids: [11, 22, 33], seat_colors: ['blue', 'red', 'green'], turn: 2 };
  const active = h.shellPlayers(room, 11, state);
  assert.equal(active[0].active, false); assert.equal(active[1].active, true);
  assert.equal(active[1].color, theme.playerColors.green);
  assert.equal(h.shellPlayers(room, 11, { ...state, winner: 0 }).some(p => p.active), false);
});
