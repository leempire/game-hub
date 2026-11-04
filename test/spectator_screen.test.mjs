import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const React = require('react'), ts = require('typescript');
const { renderToStaticMarkup } = require('react-dom/server');

function harness(password = false) {
  const slots = [], effects = [], connections = [], grants = [], elements = new Map();
  let cursor = 0, focused = true;
  const state = initial => { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
    return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; };
  const hooks = { ...React, useState: state, useRef: initial => state(() => ({ current: initial }))[0],
    useEffect(effect, deps) { const i = cursor++; const prev = slots[i];
      if (!prev || deps.some((d, j) => d !== prev.deps[j])) { effects.push(() => { prev?.cleanup?.(); slots[i] = { deps, cleanup: effect() }; }); } } };
  const host = name => props => { if (props.testID) elements.set(props.testID, props); return React.createElement(name, null, props.children); };
  const room = { code: 'ABC123', game: 'uno', allow_spectators: true, has_password: password,
    players: [{ id: 9, username: 'player' }] };
  class Connection {
    constructor(...args) { this.args = args; connections.push(this); }
    connect() { this.connected = true; }
    close() { this.closed = true; }
  }
  let backs = 0;
  const modules = {
    react: hooks,
    'react-native': { View: host('div'), Text: host('span'), ScrollView: host('section'), TouchableOpacity: host('button'),
      ActivityIndicator: () => null, StyleSheet: { create: x => x } },
    '@react-navigation/native': { useNavigation: () => ({ canGoBack: () => true, goBack: () => backs++ }), useIsFocused: () => focused },
    'react-native-safe-area-context': { useSafeAreaInsets: () => ({ bottom: 0 }) },
    '../auth': { useAuth: () => ({ token: 'test', user: { id: 1 } }) },
    '../api': { getRoom: async () => room },
    '../social': { requestWatch: async (...args) => { grants.push(args); if (password && args[2] !== 'correct') throw Error('密码错误'); return { grant: 'g', ws_path: '/watch' }; } },
    '../watchConnection': { default: Connection, __esModule: true },
    '../GameShell': { default: ({ children, status, actions }) => React.createElement('div', null, status, children, actions), __esModule: true },
    '../StasisWatchMap': { default: () => null, __esModule: true },
    '../WatchUnoTable': { default: host('table'), __esModule: true },
    '../ui': { Card: host('article'), FormInput: host('input') },
    '../theme': require('../app/src/theme.ts'),
  };
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL('../app/src/screens/SpectatorScreen.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
  }).outputText;
  runInNewContext(code, { exports, require: name => { assert.ok(name in modules, `Unexpected dependency ${name}`); return modules[name]; }, setTimeout, clearTimeout });
  function render() { cursor = 0; elements.clear(); renderToStaticMarkup(exports.default({ route: { params: { code: 'abc123' } } })); effects.splice(0).forEach(fn => fn()); }
  return { render, async settle() { for (let i = 0; i < 4; i++) { await new Promise(resolve => setImmediate(resolve)); render(); } },
    focus(value) { focused = value; render(); }, connections, grants, elements, get backs() { return backs; },
    unmount() { slots.forEach(slot => slot?.cleanup?.()); } };
}

test('spectator focus owns connection lifetime and exit only navigates', async () => {
  const h = harness(); h.render(); await h.settle();
  assert.equal(h.connections.length, 1); assert.equal(h.grants[0][1], 'ABC123');
  h.focus(false); assert.equal(h.connections[0].closed, true);
  await h.settle(); assert.equal(h.connections.length, 1);
  h.focus(true); await h.settle(); assert.equal(h.connections.length, 2);
  h.elements.get('watch-leave').onPress(); assert.equal(h.backs, 1);
  h.unmount(); assert.equal(h.connections[1].closed, true);
});

test('password watch retries inline without joining or creating player sockets', async () => {
  const h = harness(true); h.render(); await h.settle(); assert.equal(h.grants.length, 0);
  h.elements.get('watch-password').onChangeText('wrong'); h.render(); h.elements.get('watch-authorize').onPress();
  await h.settle(); assert.equal(h.connections.length, 0); assert.ok(h.elements.has('watch-error'));
  h.elements.get('watch-password').onChangeText('correct'); h.render(); h.elements.get('watch-authorize').onPress();
  await h.settle(); assert.equal(h.connections.length, 1); assert.equal(h.grants.length, 2); h.unmount();
});
