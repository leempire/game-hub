import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const ts = require('typescript');
const theme = require('../app/src/theme.ts');
function mount(file, props, extra = {}) {
  let cursor = 0, tree;
  const slots = [], effects = [], cleanups = [], nodes = new Map();
  const state = value => {
    const i = cursor++;
    if (!(i in slots)) slots[i] = typeof value === 'function' ? value() : value;
    return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
  };
  const hooks = {
    useState: state, useRef: value => state(() => ({ current: value }))[0],
    useEffect(fn, deps) { const i = cursor++; if (!slots[i] || deps.some((v, j) => v !== slots[i][j])) { slots[i] = deps; effects.push(fn); } },
    useCallback: fn => fn,
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; },
  };
  const modules = {
    react: hooks, 'react-native': { StyleSheet: { create: v => v }, View: 'View', Text: 'Text', TouchableOpacity: 'Button' },
    './ui': { AppModal: 'Modal' }, './theme': theme,
    '@react-navigation/native': { useIsFocused: () => true }, ...extra,
  };
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(new URL(`../app/src/${file}.tsx`, import.meta.url), 'utf8'), {
    compilerOptions: { jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText, { exports, require: key => { assert.ok(key in modules, key); return modules[key]; }, setInterval, clearInterval, Date, Error });
  function walk(node) {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node.type === 'Modal' && !node.props.visible) return;
    if (node.props?.testID) nodes.set(node.props.testID, node.props);
    walk(node.props?.children);
  }
  function render() { cursor = 0; nodes.clear(); tree = exports.default(props); walk(tree); while (effects.length) { const cleanup = effects.shift()(); if (cleanup) cleanups.push(cleanup); } return tree; }
  return { render, get: id => { render(); assert.ok(nodes.has(id), id); return nodes.get(id); },
    has: id => { render(); return nodes.has(id); }, press: id => { const node = nodes.get(id) ?? (render(), nodes.get(id)); assert.ok(node, id); if (!node.disabled) node.onPress(); },
    unmount: () => cleanups.forEach(fn => fn()), props };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

test('快捷语只发送目录ID，阻止并发和冷却期重复请求，实时收到其他人消息', async () => {
  const calls = [], handlers = {};
  let resolve;
  const view = mount('QuickChatControls', { token: 'token', code: 'ROOM', phrases: [{ id: 'hello', text: '你好' }], enabled: true,
    getSocket: () => ({ on: (key, fn) => { handlers[key] = fn; }, off: key => { delete handlers[key]; } }) }, {
    './social': { sayPhrase: (...args) => { calls.push(args); return new Promise(r => { resolve = r; }); } },
  });
  view.render(); view.press('quick-chat-open'); view.render();
  view.press('quick-phrase-hello'); view.press('quick-phrase-hello');
  assert.deepEqual(calls, [['token', 'ROOM', 'hello']]);
  resolve({ cooldown_seconds: 2, message: { name: '我', text: '你好', id: 'hello' } }); await flush();
  assert.equal(view.has('quick-chat-sheet'), false);
  view.press('quick-chat-open');
  assert.equal(view.get('quick-phrase-hello').disabled, true);
  handlers.quick_chat({ type: 'quick_chat', name: '队友', text: '需要帮助', id: 'help' });
  assert.match(JSON.stringify(view.get('quick-chat-message').children), /队友.*需要帮助/);
  view.unmount(); assert.equal(handlers.quick_chat, undefined);
});

test('断线不可发快捷语；发送错误保留选择面板供重试', async () => {
  let calls = 0;
  const view = mount('QuickChatControls', { token: 'token', code: 'R', phrases: [{ id: 'thanks', text: '谢谢' }], enabled: false, getSocket: () => null }, {
    './social': { sayPhrase: async () => { calls++; throw new Error('稍后再试'); } },
  });
  view.render(); view.press('quick-chat-open'); view.render(); view.press('quick-phrase-thanks'); assert.equal(calls, 0);
  view.props.enabled = true; view.render(); view.press('quick-phrase-thanks'); await flush();
  assert.equal(calls, 1); assert.equal(view.has('quick-chat-sheet'), true);
  assert.match(JSON.stringify(view.render()), /稍后再试/); view.unmount();
});

test('指引最多三步，跳过按账号和游戏记忆；练习请求并发去重并显示失败', async () => {
  const stored = new Map(), calls = [];
  let reject;
  const view = mount('GameIntro', { game: 'uno', userId: 7, practice: false,
    onPractice: () => { calls.push('practice'); return new Promise((_, r) => { reject = r; }); } }, {
    '@react-native-async-storage/async-storage': { getItem: async key => stored.get(key) ?? null, setItem: async (key, value) => { stored.set(key, value); } },
  });
  view.render(); await flush(); assert.equal(view.has('game-intro'), true);
  view.press('guide-next'); view.render(); view.press('guide-next');
  assert.match(JSON.stringify(view.render()), /3," \/ ",3/);
  view.press('guide-skip'); assert.equal(view.has('game-intro'), false);
  assert.equal(stored.get('gamehub:guide:v1:7:uno'), 'seen');
  view.press('start-practice'); view.press('start-practice'); assert.equal(calls.length, 1);
  reject(new Error('创建失败')); await flush(); assert.match(JSON.stringify(view.render()), /创建失败/);
  view.unmount();
});
