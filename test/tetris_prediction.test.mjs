// node --test test/tetris_prediction.test.mjs（Node >= 22.18；先安装 app/ 的依赖）
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import * as tetris from '../app/src/tetris.ts';

const require = createRequire(new URL('../app/package.json', import.meta.url));
const ts = require('typescript');
// 形状和期望结果直接来自服务端引擎，不在测试里另抄一套规则。
const fixtures = JSON.parse(execFileSync('python', ['-c', `
import copy, json
from app.game import tetris as t
cases = []
actions = ['left', 'rotate_cw'] + ['down'] * 8 + ['right', 'rotate_ccw']
for piece in t.PIECES:
    for count in (1, 2, 4):
        gs = t.create_game({1: 42, 2: 42}, [1, 2])
        for uid in (1, 2):
            t.spawn_next(gs, uid)
        gs['current'][2] = t.spawn(piece)
        before = copy.deepcopy(gs)
        for action in actions:
            t.apply_action(before, 2, action)
        t.deliver_garbage(gs, 1, count)
        incoming = copy.deepcopy(t.game_to_sync(gs, 2))
        for action in actions:
            t.apply_action(gs, 2, action)
        cases.append(dict(incoming=incoming, actions=actions,
                          before=before['current'][2], expected=gs['current'][2]))
print(json.dumps(cases))
`], { cwd: fileURLToPath(new URL('../server/', import.meta.url)), encoding: 'utf8' }));

const base = fixtures.find(({ incoming }) => incoming.current.piece === 'T').incoming;
const empty = () => Array.from({ length: 20 }, () => Array(10).fill(0));
const garbage = (count) => {
  const board = empty();
  for (let row = 20 - count; row < 20; row += 1) board[row].fill(8, 1);
  return board;
};
const initial = (extra = {}) => ({ ...base, board: empty(), garbage_received: 0, ...extra });
const packet = (extra = {}) => ({
  ...initial(), user_id: 2, last_action: 'garbage', garbage_count: 1,
  board: garbage(1), garbage_received: 1, ...extra,
});

// 挂载实际对局组件，只替换原生渲染、网络和时钟；通过它注册的 WS/键盘回调驱动。
const screenCode = ts.transpileModule(
  readFileSync(new URL('../app/src/screens/TetrisScreen.tsx', import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } },
).outputText;
function mount(game = initial()) {
  const states = [], effects = [], sent = [], listeners = new Map();
  let keys;
  const React = {
    createElement: () => null,
    useState(value) {
      const state = { value };
      states.push(state);
      return [value, (next) => {
        state.value = typeof next === 'function' ? next(state.value) : next;
      }];
    },
    useRef: (current) => ({ current }),
    useMemo: (fn) => fn(), useCallback: (fn) => fn,
    useEffect: (fn) => effects.push(fn),
  };
  class RoomSocket {
    on(type, callback) { listeners.set(type, callback); }
    connect() {}
    close() {}
    tetrisActions(actions) { sent.push(Array.from(actions)); }
  }
  const theme = { colors: {}, spacing: {}, radius: {}, fontSize: {}, playerColors: [] };
  const imports = {
    react: React,
    'react-native': { StyleSheet: { create: (styles) => styles }, Platform: { OS: 'web' },
      useWindowDimensions: () => ({ width: 390, height: 844 }),
      PanResponder: { create: () => ({ panHandlers: {} }) } },
    '@expo/vector-icons': {},
    '@react-navigation/native': { useNavigation: () => ({ addListener: () => () => {} }) },
    '../auth': { useAuth: () => ({ token: 'test', user: { id: 2 } }) },
    '../api': { getRoom: async () => null },
    '../socket': { RoomSocket }, '../theme': theme,
    '../utils': {}, '../help': {}, '../tetris': tetris,
    '../backGuard': { useModalBackGuard() {}, onConfirmLeave() {},
      useWebKeys: (handler) => { keys = handler; } },
  };
  const exports = {};
  runInNewContext(screenCode, {
    exports, require: (name) => {
      assert.ok(name in imports, `unmocked import: ${name}`);
      return imports[name];
    }, setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  });
  exports.default({ code: 'TEST01', onLeave() {} });
  effects.forEach((effect) => effect());
  listeners.get('sync')({ game });
  return {
    send: (message) => listeners.get('tetris_state')(message),
    key: (key) => keys(key, { repeat: false }), sent,
    view: () => states.find(({ value }) => value && typeof value === 'object' && 'cur' in value).value,
  };
}

for (const { incoming, actions, before, expected } of fixtures) {
  test(`${incoming.current.piece}: 收到 ${incoming.garbage_received} 行后保留位移/旋转/下落`, () => {
    const rules = { shapes: incoming.shapes, kicks: incoming.kicks };
    const replay = tetris.replayPending(incoming.board, rules, incoming.current, actions);
    assert.deepEqual(replay.current, before);
    assert.deepEqual(replay.current, expected);
    assert.deepEqual(replay.actions, actions);
    assert.equal(replay.locked, false);
  });
}

test('连续受攻击后不丢输入，落定时仍把整段动作交给服务端', () => {
  const screen = mount();
  for (const key of ['ArrowLeft', 'ArrowUp', 'ArrowDown', 'ArrowDown']) screen.key(key);
  const before = screen.view().cur;
  screen.send(packet());
  assert.deepEqual(screen.view().cur, before);
  screen.key('ArrowDown');
  const second = screen.view().cur;
  screen.send(packet({ board: garbage(3), garbage_received: 3, garbage_count: 2 }));
  assert.deepEqual(screen.view().cur, second);
  screen.key(' ');
  assert.deepEqual(screen.sent, [['left', 'rotate_cw', 'down', 'down', 'down', 'hard_drop']]);
});

for (const [key, action, reply] of [[' ', 'hard_drop', 'lock'], ['c', 'hold', 'hold']]) {
  test(`等待 ${reply} 回包时收到攻击不能解冻或重复发送动作`, () => {
    const screen = mount();
    screen.key(key);
    const waiting = screen.view();
    screen.send(packet());
    assert.equal(screen.view(), waiting);
    screen.key('ArrowLeft');
    screen.key(' ');
    assert.deepEqual(screen.sent, [[action]]);
    screen.send(packet({ last_action: reply }));
    screen.key('ArrowLeft');
    screen.key(' ');
    assert.deepEqual(screen.sent, [[action], ['left', 'hard_drop']]);
  });
}

test('新盘面使重放提前落定时，截断剩余输入，不操作下一块', () => {
  const current = { piece: 'O', rotation: 0, x: 3, y: 16 };
  const screen = mount(initial({ current }));
  for (const key of ['ArrowDown', 'ArrowDown', 'ArrowLeft']) screen.key(key);
  screen.send(packet({ current }));
  assert.equal(screen.view().cur.y, 17);
  assert.equal(screen.view().cur.x, 3);
  assert.deepEqual(screen.sent, [['down', 'down']]);
  screen.key(' ');
  assert.equal(screen.sent.length, 1);
});

test('攻击导致出局时仍采用权威终局，不继续重放', () => {
  const screen = mount();
  screen.key(' ');
  screen.send(packet({ finished: true, finish_reason: 'garbage', current: null }));
  assert.equal(screen.view().cur, null);
  screen.key('ArrowDown');
  screen.key(' ');
  assert.equal(screen.sent.length, 1);
});
