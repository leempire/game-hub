// Exercise the real room component's REST/WS/focus races, not an independent navigation model.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const React = require('react'), ts = require('typescript');
const { renderToStaticMarkup } = require('react-dom/server');
const theme = require('../app/src/theme.ts');
const room = { code: 'ENTRY1', game: 'gomoku', status: 'playing', max_players: 2, host_id: 1,
  name: 'test', players: [{ id: 1, username: 'a', is_host: true, connected: true },
    { id: 2, username: 'b', connected: true }], in_game: true };
const settle = () => new Promise(resolve => setImmediate(resolve));
function harness() {
  let cursor = 0, props = {}, focusEffect;
  const slots = [], effects = [], sockets = [], requests = [], joins = [], entries = [];
  const useState = initial => {
    const i = cursor++;
    if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
    return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
  };
  const useMemo = (factory, deps) => {
    const i = cursor++;
    if (!slots[i] || deps.some((dep, j) => dep !== slots[i].deps[j])) slots[i] = { value: factory(), deps };
    return slots[i].value;
  };
  const useEffect = (effect, deps) => {
    const i = cursor++;
    if (!slots[i] || deps.some((dep, j) => dep !== slots[i].deps[j])) {
      const previous = slots[i]; slots[i] = { deps };
      effects.push(() => { previous?.cleanup?.(); slots[i].cleanup = effect(); });
    }
  };
  const hooks = { ...React, useState, useMemo, useEffect,
    useRef: initial => useState(() => ({ current: initial }))[0],
    useCallback: (fn, deps) => useMemo(() => fn, deps) };
  const host = props => React.createElement('div', {}, props.children);
  class RoomSocket {
    handlers = {}; connected = 0; closed = 0;
    constructor() { sockets.push(this); }
    on(name, fn) { this.handlers[name] = fn; }
    connect() { this.connected++; }
    close() { this.closed++; }
    pause() {} resume() {} reconnect() {} start() {}
  }
  const modules = {
    react: hooks,
    'react-native': { View: host, Text: host, TouchableOpacity: host, ScrollView: host,
      ActivityIndicator: host, Modal: () => null, Platform: { OS: 'web' }, StyleSheet: { create: s => s } },
    '@expo/vector-icons': { Ionicons: host }, 'expo-clipboard': { setStringAsync: async () => {} },
    '@react-navigation/native': { useNavigation: () => ({ replace() {} }), useFocusEffect: callback => {
      focusEffect = callback; useEffect(callback, [callback]);
    } },
    '../GameIntro': { __esModule: true, default: () => null }, '../social': { createPractice: async () => ({ code: 'practice' }) },
    '../auth': { useAuth: () => ({ token: 'test', user: { id: 1 } }) },
    '../api': { getRoom: () => new Promise(resolve => requests.push(resolve)),
      joinRoom: () => new Promise(resolve => joins.push(resolve)), leaveRoom: async () => {} },
    '../socket': { RoomSocket }, '../backGuard': { useModalBackGuard() {} },
    '../theme': theme, '../utils': { notify() {} },
    '../roomSettings': { RoomSettingsRow: () => null, RoomSettingsSheet: () => null,
      settingsSummary: () => '', settingValueLabel: () => '' },
    '../help': { GameHelpButton: () => null }, '../games': { gameTitle: game => game },
    '../InviteFriendsModal': { __esModule: true, default: () => null },
  };
  const exports = {};
  const source = ts.transpileModule(readFileSync(new URL('../app/src/screens/RoomScreen.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText;
  runInNewContext(source, { exports, require: name => {
    assert.ok(name in modules, `Unexpected import: ${name}`); return modules[name];
  }, setTimeout, clearTimeout });
  const defaultProps = { code: 'ENTRY1', onLeave() {}, onGameStart: (...args) => entries.push(args) };
  return { sockets, requests, joins, entries,
    render(next = {}) {
      props = { ...defaultProps, ...props, ...next }; cursor = 0;
      renderToStaticMarkup(exports.default(props));
      for (const effect of effects.splice(0)) effect();
    },
    message(type, data = {}) { sockets[0].handlers[type]({ type, room, game: {}, in_game: true, ...data }); },
    focus() { return focusEffect(); },
  };
}
test('REST, sync and game_start enter once; new wrapper callbacks never reconnect the room socket', async () => {
  const h = harness(); h.render();
  h.requests.shift()(room); await settle();
  assert.equal(h.sockets[0].connected, 1);
  h.message('sync'); h.message('game_start');
  assert.equal(h.entries.length, 1);
  h.render({ onGameStart: (...args) => h.entries.push(args), onLeave() {} });
  assert.equal(h.sockets.length, 1);
  // Returning to this still-mounted room permits exactly one entry for a new round.
  h.focus();
  h.message('sync'); h.message('game_start');
  assert.equal(h.entries.length, 2);
  h.requests.shift()(room); await settle();
  assert.equal(h.entries.length, 2);
});
test('a late REST playing snapshot cannot re-enter after a newer lobby sync', async () => {
  const h = harness(); h.render(); h.requests.shift()(room); await settle();
  h.focus();
  h.message('sync', { room: { ...room, status: 'waiting' }, game: null });
  h.requests.shift()(room); await settle();
  assert.equal(h.entries.length, 0);
});
test('deep link finishes joining before opening a member-only WebSocket', async () => {
  const h = harness(); h.render();
  h.requests.shift()({ ...room, status: 'waiting', players: [room.players[1]] }); await settle();
  assert.equal(h.joins.length, 1); assert.equal(h.sockets[0].connected, 0);
  h.joins.shift()({ ...room, status: 'waiting' }); await settle();
  assert.equal(h.sockets[0].connected, 1);
});
