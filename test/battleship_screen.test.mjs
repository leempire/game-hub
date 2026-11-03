// node --test test/battleship_screen.test.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';

const require = createRequire(new URL('../app/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');
const source = readFileSync(new URL('../app/src/screens/BattleshipScreen.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true,
} }).outputText;

function screen(os) {
  // 仅替换平台与 hook 调度，执行整份生产组件、WS 回调及其所有子组件。
  const slots = [], pending = [], sockets = [], cards = [];
  let cursor = 0;
  const useState = initial => {
    const index = cursor++;
    if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
    return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
  };
  const hooks = {
    ...React, useState,
    useRef: initial => useState(() => ({ current: initial }))[0],
    useMemo: (factory, deps) => {
      const index = cursor++;
      if (!slots[index] || deps.some((dep, i) => dep !== slots[index].deps[i])) {
        slots[index] = { value: factory(), deps };
      }
      return slots[index].value;
    },
    useCallback: callback => { cursor++; return callback; },
    useEffect: (effect, deps) => {
      const index = cursor++;
      if (!slots[index] || deps.some((dep, i) => dep !== slots[index][i])) {
        slots[index] = deps;
        pending.push(effect);
      }
    },
  };
  const host = name => props => {
    if (props.accessibilityRole === 'button') cards.push(props);
    return React.createElement(name, { 'data-testid': props.testID }, props.children);
  };
  const room = { players: [{ id: 1, connected: true }, { id: 2, connected: true }] };
  const navigation = { addListener: () => () => {} };
  const rn = {
    View: host('div'), Text: host('span'), TouchableOpacity: host('button'),
    ScrollView: host('section'), Modal: ({ visible, children }) => visible ? children : null,
    StyleSheet: { create: styles => styles }, Platform: { OS: os },
    useWindowDimensions: () => ({ width: 390, height: 844 }),
    PanResponder: { create: handlers => ({ panHandlers: handlers }) },
  };
  class RoomSocket {
    handlers = {};
    constructor() { sockets.push(this); }
    on(name, callback) { this.handlers[name] = callback; }
    connect() {}
  }
  const exports = {};
  const modules = {
    react: hooks, 'react-native': rn, '@expo/vector-icons': { Ionicons: host('i') },
    '../auth': { useAuth: () => ({ token: 'test', user: { id: 1 } }) },
    '../api': { getRoom: async () => room }, '../socket': { RoomSocket },
    '../theme': require('../app/src/theme.ts'), '../utils': { notify() {} },
    '@react-navigation/native': { useNavigation: () => navigation },
    '../backGuard': { useModalBackGuard() {}, onConfirmLeave: () => () => {} },
    '../help': { GameHelpButton: () => null }, '../disconnect': { useDisconnectCountdown: () => 120 },
  };
  runInNewContext(code, { exports, require: name => {
    assert.ok(name in modules, `Unexpected dependency: ${name}`);
    return modules[name];
  }, setTimeout, clearTimeout });
  const onLeave = () => {};
  return {
    receive: (kind, game) => sockets[0].handlers[kind]({ game, room }),
    selectShip(index) {
      cards[index].onPanResponderGrant({}, { x0: 0, y0: 0 });
      cards[index].onPanResponderRelease();
    },
    selectedShip: () => cards.findIndex(card => card.accessibilityState.selected),
    render() {
      cursor = 0;
      cards.length = 0;
      const tree = exports.default({ code: 'TEST01', onLeave });
      const html = renderToStaticMarkup(tree);
      for (const effect of pending.splice(0)) effect();
      return html;
    },
  };
}

const fleets = {
  6: [[1, 'cruiser', 3], [2, 'submarine', 2], [3, 'destroyer', 2], [4, 'patrol', 2]],
  10: [[1, 'carrier', 5], [2, 'battleship', 4], [3, 'cruiser', 3], [4, 'submarine', 3], [5, 'destroyer', 2]],
  12: [[1, 'carrier', 5], [2, 'battleship', 4], [3, 'cruiser', 3], [4, 'submarine', 3], [5, 'destroyer', 2], [6, 'patrol', 2]],
};
const weaponShips = { 6: [4, 3, 2], 10: [5, 4, 3], 12: [6, 5, 4] };
for (const os of ['android', 'web']) {
  for (const size of [6, 10, 12]) {
    for (const kind of ['sync', 'game_start']) {
      test(`${os} ${size}×${size} ${kind}: 开局布阵不崩溃且使用本局舰队`, () => {
        const view = screen(os);
        view.render();
        view.receive(kind, {
          board_size: size, fleet: fleets[size].map(([id, name, size]) => ({ id, name, size })),
          phase: 'placing', your_color: 'blue', placement_ready: {},
          own_weapons: Object.fromEntries(['radar', 'salvo', 'strike'].map((key, index) =>
            [key, { ship_id: weaponShips[size][index] }])),
        });
        const html = view.render();
        assert.equal((html.match(/data-testid="bs-placement-\d+-\d+"/g) || []).length, size * size);
        assert.match(html, /完成布阵/);
        assert.equal(html.includes('巡逻艇'), size !== 10);
        assert.equal(html.includes('航母'), size !== 6);
        assert.ok(!html.includes('—'), '武器应挂靠本局实际存在的舰船');
        view.selectShip(fleets[size].length - 1);
        view.render();
        assert.equal(view.selectedShip(), fleets[size].length - 1);
      });
    }
  }
}
