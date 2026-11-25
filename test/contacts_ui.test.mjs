import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const ts = require('typescript'), theme = require('../app/src/theme.ts');
const source = file => ts.transpileModule(readFileSync(new URL(`../app/src/${file}`, import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
}).outputText;
const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

test('foreground polling never overlaps; background and cleanup invalidate pending responses', async () => {
  const intervals = new Map(), listeners = new Set(), pending = [], errors = [];
  const AppState = { currentState: 'active', addEventListener: (_, fn) => {
    listeners.add(fn); return { remove: () => listeners.delete(fn) };
  } };
  const exports = {};
  runInNewContext(source('useForegroundPolling.ts'), { exports,
    require: key => ({ react: {}, 'react-native': { AppState }, '@react-navigation/native': {} }[key]),
    setInterval: fn => { intervals.set(1, fn); return 1; }, clearInterval: id => intervals.delete(id) });
  const stop = exports.startForegroundPolling(valid => new Promise((resolve, reject) => pending.push({ valid, resolve, reject })), value => errors.push(value), 2000);
  assert.equal(pending.length, 1);
  intervals.get(1)(); intervals.get(1)(); assert.equal(pending.length, 1);
  const change = next => { AppState.currentState = next; for (const fn of listeners) fn(next); };
  change('background'); assert.equal(pending[0].valid(), false);
  pending[0].reject(new Error('old request')); await flush(); assert.equal(errors.length, 0);
  intervals.get(1)(); assert.equal(pending.length, 1);
  change('active'); assert.equal(pending.length, 2); assert.equal(pending[1].valid(), true);
  stop(); assert.equal(pending[1].valid(), false); assert.equal(intervals.size, 0); assert.equal(listeners.size, 0);
  pending[1].resolve(); await flush();
});

function mount(file, props, overrides) {
  const slots = [], effects = [], cleanups = [], nodes = new Map(), people = new Map();
  let cursor = 0, tree;
  const useState = initial => {
    const index = cursor++;
    if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
    return [slots[index], next => { slots[index] = typeof next === 'function' ? next(slots[index]) : next; }];
  };
  const React = { useState, useRef: initial => useState(() => ({ current: initial }))[0],
    useEffect(fn, deps) { const index = cursor++; if (!slots[index] || deps.some((value, i) => value !== slots[index][i])) { slots[index] = deps; effects.push(fn); } },
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; } };
  const modules = { react: React, 'react-native': { View: 'View', Text: 'Text', TouchableOpacity: 'Button', ActivityIndicator: 'Spinner',
      ScrollView: 'Scroll', StyleSheet: { create: value => value } },
    './ui': { AppModal: 'Modal', FormInput: 'Input' }, '../ui': { AppModal: 'Modal', Card: 'Card' },
    './SocialCards': { PersonCard: 'Person', SocialButton: 'Button' },
    '../SocialCards': { PersonCard: 'Person', SocialButton: 'Button', SocialEmpty: 'Empty' },
    './theme': theme, '../theme': theme,
    '../games': { gameTitle: key => key },
    '../api': { joinRoom: async (_token, code) => ({ code }) },
    ...overrides };
  const exports = {};
  runInNewContext(source(file), { exports, require: key => { assert.ok(key in modules, key); return modules[key]; }, Error });
  const walk = node => {
    if (Array.isArray(node)) return node.forEach(walk);
    if (!node || typeof node !== 'object' || (node.type === 'Modal' && !node.props.visible)) return;
    if (node.props?.testID) nodes.set(node.props.testID, node.props);
    if (node.type === 'Person') people.set(node.props.person.id, node.props);
    walk(node.props?.children);
  };
  const render = () => { cursor = 0; nodes.clear(); people.clear(); tree = exports.default(props); walk(tree); while (effects.length) { const clean = effects.shift()(); if (clean) cleanups.push(clean); } return tree; };
  const get = id => { render(); assert.ok(nodes.has(id), id); return nodes.get(id); };
  return { render, get, press: id => { const node = get(id); if (!node.disabled && !node.busy) node.onPress(); },
    has: id => { render(); return nodes.has(id); }, person: id => { render(); return people.get(id); },
    unmount: () => cleanups.forEach(fn => fn()), text: () => JSON.stringify(render()) };
}

test('contact search submits once, preserves failed request for retry, and suppresses closed results', async () => {
  let resolve, calls = 0;
  const api = { searchContacts: () => { calls++; return new Promise(done => { resolve = done; }); },
    requestFriend: async () => { throw new Error('稍后再试'); } };
  const props = { visible: true, token: 't', onClose() {}, onChanged() {}, onChat() {} };
  const view = mount('ContactSearchModal.tsx', props, { './contacts': api });
  view.render(); view.get('social-search-input').onChangeText('friend');
  view.press('social-search-submit'); view.press('social-search-submit'); assert.equal(calls, 1);
  resolve({ items: [{ id: 2, username: 'friend', nickname: null, relation: 'none' }] }); await flush();
  assert.equal(view.has('social-search-result-2'), true);
  view.press('social-search-action-2'); await flush(); assert.match(view.text(), /稍后再试/);
  assert.equal(view.get('social-search-action-2').disabled, false);
  view.press('social-search-submit'); view.press('social-search-close'); view.unmount();
  resolve({ items: [{ id: 3, username: 'late', relation: 'none' }] }); await flush();
  assert.equal(view.has('social-search-result-3'), false);
});

test('friend management requires confirmation and blocks duplicate mutations', async () => {
  let poll, refreshes = 0, resolve, blocks = 0;
  const person = { id: 2, username: 'friend', nickname: null, unread_count: 0, last_message: null };
  const api = { displayContact: p => p.nickname || p.username, getSocial: async () => ({ friends: [person], incoming: [], outgoing: [], blocked: [], unread_total: 0 }),
    blockContact: () => { blocks++; return new Promise(done => { resolve = done; }); } };
  const view = mount('screens/SocialTab.tsx', {}, { '../contacts': api,
    '../auth': { useAuth: () => ({ token: 't' }) },
    '@react-navigation/native': { useIsFocused: () => true, useNavigation: () => ({ navigate() {} }) },
    '../useForegroundPolling': { __esModule: true, default: (_, task) => { poll = task; return () => { refreshes++; }; } },
    '../ContactSearchModal': { __esModule: true, default: 'Search' } });
  view.render(); await poll(() => true); view.press('social-segment-friends');
  view.person(2).onMore(); view.press('social-block'); assert.equal(blocks, 0);
  assert.equal(view.has('social-confirm'), true);
  view.press('social-confirm'); view.press('social-confirm'); assert.equal(blocks, 1);
  resolve({ ok: true }); await flush(); assert.equal(view.has('social-manage-sheet'), false); assert.equal(refreshes, 1);
  view.unmount();
});

test('room invitation joins through the normal room API and can be dismissed', async () => {
  let poll, refreshes = 0;
  const joins = [], declines = [], navigations = [];
  const invite = { id: 7, inviter: { id: 2, username: 'friend', nickname: null },
    room: { code: 'ABC123', name: '周末房', game: 'gomoku', status: 'waiting', players: 1, max_players: 2 },
    created_at: '2026-11-13T00:00:00Z' };
  const api = { displayContact: p => p.nickname || p.username,
    getSocial: async () => ({ friends: [], incoming: [], outgoing: [], blocked: [], unread_total: 0, invites: [invite] }),
    declineRoomInvite: async (_token, id) => { declines.push(id); return { ok: true }; } };
  const view = mount('screens/SocialTab.tsx', {}, { '../contacts': api,
    '../api': { joinRoom: async (_token, code) => { joins.push(code); return { code }; } },
    '../auth': { useAuth: () => ({ token: 't' }) },
    '@react-navigation/native': { useIsFocused: () => true,
      useNavigation: () => ({ navigate: (name, params) => navigations.push({ name, params }) }) },
    '../useForegroundPolling': { __esModule: true, default: (_, task) => { poll = task; return () => { refreshes++; }; } },
    '../ContactSearchModal': { __esModule: true, default: 'Search' } });
  view.render(); await poll(() => true);
  assert.equal(view.has('social-invite-7'), true);
  view.press('social-invite-join-7'); await flush();
  assert.deepEqual(joins, ['ABC123']);
  // 导航回调在 vm 上下文里创建，跨 realm 的对象不能直接 deepEqual。
  assert.equal(navigations.length, 1);
  assert.equal(navigations[0].name, 'Room');
  assert.equal(navigations[0].params.code, 'ABC123');
  view.press('social-invite-decline-7'); await flush();
  assert.deepEqual(declines, [7]);
  assert.equal(refreshes, 2);
  view.unmount();
});
