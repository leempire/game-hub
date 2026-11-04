import { installGameChrome } from './load_game_chrome.mjs';
// node --test test/uttt_screen.test.mjs
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';

const require = createRequire(new URL('../app/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');
const theme = require('../app/src/theme.ts');

function screen(os = 'web', width = 390, height = 844) {
  const slots = [], effects = [], sockets = [], elements = new Map(), navigationCalls = [], leaveCalls = [];
  let cursor = 0;
  const useState = initial => {
    const index = cursor++;
    if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
    return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
  };
  const useMemo = (factory, deps) => {
    const index = cursor++;
    if (!slots[index] || deps.some((dep, i) => dep !== slots[index].deps[i])) slots[index] = { value: factory(), deps };
    return slots[index].value;
  };
  const hooks = {
    ...React, useState, useMemo,
    useRef: initial => useState(() => ({ current: initial }))[0],
    useCallback: (callback, deps) => useMemo(() => callback, deps),
    useEffect: (effect, deps) => {
      const index = cursor++;
      if (!slots[index] || deps.some((dep, i) => dep !== slots[index][i])) {
        slots[index] = deps;
        effects.push(effect);
      }
    },
  };
  const host = name => props => {
    if (props.testID) elements.set(props.testID, props);
    return React.createElement(name, { 'data-testid': props.testID }, props.children);
  };
  const rn = {
    KeyboardAvoidingView: host('section'), View: host('div'), Text: host('span'), ScrollView: host('section'), TouchableOpacity: host('button'),
    StyleSheet: { create: styles => styles, absoluteFillObject: { position: 'absolute', left: 0, top: 0, right: 0, bottom: 0 } },
    Modal: ({ visible, children }) => visible ? children : null,
    Platform: { OS: os }, useWindowDimensions: () => ({ width, height }), Vibration: { vibrate() {} },
  };
  class RoomSocket {
    handlers = {}; isConnected = true; sent = [];
    constructor() { sockets.push(this); }
    on(name, callback) { this.handlers[name] = callback; }
    connect(open, close) { this.open = open; this.closed = close; open?.(); }
    utttMove(...args) { this.sent.push(args); }
    surrender() {}
    close() {}
  }
  const room = { status: 'playing', players: [{ id: 1, connected: true }, { id: 2, connected: true }] };
  const navigation = { addListener: () => () => {}, goBack() { navigationCalls.push(['goBack']); }, popTo(...args) { navigationCalls.push(args); }, navigate(...args) { navigationCalls.push(args); } };
  const modules = {
    react: hooks, 'react-native': rn,
    '../auth': { useAuth: () => ({ token: 'test', user: { id: 1 } }) },
    '../api': { getRoom: async () => room, leaveRoom: async (...args) => { leaveCalls.push(args); } }, '../socket': { RoomSocket },
    '../theme': theme, './theme': theme, '../utils': { notify() {} },
    '@react-navigation/native': { useNavigation: () => navigation },
    '../backGuard': { useModalBackGuard() {}, onConfirmLeave: () => () => {} },
    '../help': { GameHelpButton: () => null }, '../disconnect': { useDisconnectCountdown: () => 9 },
  };
  const load = path => {
    const url = new URL(path, import.meta.url);
    const exports = {};
    if (!existsSync(url)) return exports;
    const code = ts.transpileModule(readFileSync(url, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true,
    } }).outputText;
    runInNewContext(code, { exports, require: name => {
      assert.ok(name in modules, `Unexpected dependency: ${name}`);
      return modules[name];
    }, setTimeout, clearTimeout });
    return exports;
  };
  installGameChrome(modules, ts);
  modules['../uttt'] = modules['./uttt'] = load('../app/src/uttt.ts');
  modules['../UtttBoard'] = load('../app/src/UtttBoard.tsx');
  const component = load('../app/src/screens/UltimateTicTacToeScreen.tsx').default;
  assert.equal(typeof component, 'function', '终极井字棋对局组件存在');
  const onLeave = () => {};
  const view = {
    render() {
      cursor = 0;
      elements.clear();
      const html = renderToStaticMarkup(component({ code: 'TEST01', onLeave }));
      for (const effect of effects.splice(0)) effect();
      return html;
    },
    receive(type, data) { sockets[0].handlers[type]({ type, room, ...data }); },
    get: id => elements.get(id),
    press(id) {
      const element = elements.get(id);
      assert.ok(element, `Missing control: ${id}`);
      if (!element.disabled) element.onPress();
      return view.render();
    },
    disconnect() { sockets[0].isConnected = false; sockets[0].closed(); },
    socket: () => sockets[0],
    navigationCalls, leaveCalls,
  };
  view.render();
  return view;
}

const game = overrides => ({
  boards: Array.from({ length: 9 }, () => Array(9).fill(0)), local_results: Array(9).fill(0),
  active_board: 4, next: 'black', your_color: 'black', winner: null, reason: null, revision: 7, moves: [],
  legal: [{ board: 4, cell: 2, next_board: 6, winner: null }, { board: 4, cell: 3, next_board: null, winner: null }],
  ...overrides,
});
const start = (view, state = game()) => { view.receive('sync', { game: state }); view.render(); };

for (const os of ['android', 'web']) {
  test(`${os}: 选格和取消不发送；独立确认同步锁住重复点击`, () => {
    const view = screen(os);
    start(view);
    assert.match(view.press('uttt-cell-4-2'), /下一步对手：左下小盘/);
    assert.equal(view.socket().sent.length, 0);
    view.press('uttt-cancel');
    assert.equal(view.get('uttt-confirm').disabled, true);
    view.press('uttt-cell-4-3');
    const confirm = view.get('uttt-confirm').onPress;
    confirm(); confirm();
    assert.deepEqual(view.socket().sent, [[4, 3, 7]]);
  });

  test(`${os}: 同版本权威消息撤销预览，旧确认闭包无效`, () => {
    const view = screen(os);
    start(view);
    view.press('uttt-cell-4-2');
    const stale = view.get('uttt-confirm').onPress;
    view.receive('uttt_state', { game: game() });
    stale();
    view.render();
    assert.equal(view.get('uttt-confirm').disabled, true);
    assert.equal(view.socket().sent.length, 0);
  });

  test(`${os}: 断线撤销，重新打开连接后须先收到新同步`, () => {
    const view = screen(os);
    start(view);
    view.press('uttt-cell-4-2');
    const stale = view.get('uttt-confirm').onPress;
    view.disconnect(); stale(); view.render();
    assert.equal(view.get('uttt-confirm').disabled, true);
    view.socket().isConnected = true; view.socket().open?.(); view.render();
    view.press('uttt-cell-4-2');
    assert.equal(view.socket().sent.length, 0);
    assert.equal(view.get('uttt-confirm').disabled, true);
  });
}

test('只查看其他小盘；对手回合与服务端未列出的格不能预览', () => {
  const view = screen();
  start(view);
  assert.match(view.press('uttt-board-0'), /本回合不可落子/);
  assert.equal(view.get('uttt-cell-0-0').disabled, true);
  view.press('uttt-board-4');
  assert.equal(view.get('uttt-cell-4-0').disabled, true);
  start(view, game({ next: 'white' }));
  assert.equal(view.get('uttt-cell-4-2').disabled, true);
});

test('自由选盘与终局目的地均使用服务端预览字段', () => {
  const view = screen();
  start(view, game({ active_board: null }));
  assert.match(view.press('uttt-cell-4-3'), /任意未结束的小盘/);
  start(view, game({ legal: [{ board: 4, cell: 2, next_board: 6, winner: 'black' }] }));
  const html = view.press('uttt-cell-4-2');
  assert.match(html, /本手将结束对局：X 胜/);
  assert.ok(!html.includes('下一步对手：左下小盘'));
});

test('重连同步已回大厅且无棋局时显示返回按钮，不误跳或恢复旧确认', () => {
  const view = screen();
  start(view);
  view.press('uttt-cell-4-2');
  view.receive('sync', { game: null, room: { status: 'waiting', players: [] } });
  const html = view.render();
  assert.match(html, /本局已结束或尚未开始/);
  assert.ok(view.get('uttt-back-from-lobby'));
  assert.equal(view.get('uttt-confirm').disabled, true);
  assert.equal(view.socket().sent.length, 0);
  assert.equal(view.navigationCalls.length, 0);
});

test('深链接和结算返回指定房间，保留席位且不调用退出接口', () => {
  for (const finished of [false, true]) {
    const view = screen();
    if (finished) start(view, game({ winner: 'black', reason: 'three_boards', legal: [] }));
    else view.receive('sync', { game: null, room: { status: 'waiting', players: [] } });
    view.render();
    view.press(finished ? 'uttt-back-to-room' : 'uttt-back-from-lobby');
    assert.equal(JSON.stringify(view.navigationCalls), JSON.stringify([['Room', { code: 'TEST01' }]]));
    assert.equal(view.leaveCalls.length, 0);
  }
});

for (const [width, height] of [[320, 640], [390, 844], [1280, 900]]) {
  test(`${width}×${height}: 放大格至少44，主区可滚动且底部有独立操作`, () => {
    const view = screen('web', width, height);
    start(view);
    const style = Object.assign({}, ...view.get('uttt-cell-4-2').style.flat(Infinity).filter(Boolean));
    assert.ok(style.width >= 44 && style.height >= 44);
    assert.ok(view.get('uttt-scroll'));
    assert.ok(view.get('uttt-actions'));
    assert.ok(view.get('uttt-surrender'));
    assert.ok(view.get('uttt-leave'));
  });
}

for (const os of ['android', 'web']) {
  test(`${os}: 结算查看战绩先回指定房间，保留席位与返回路径`, () => {
    const view = screen(os);
    start(view, game({ winner: 'black', reason: 'three_boards', legal: [] }));
    view.press('game-result-history');
    assert.equal(JSON.stringify(view.navigationCalls), JSON.stringify([['Room', { code: 'TEST01' }], ['History']]));
    assert.equal(view.leaveCalls.length, 0);
  });
}
