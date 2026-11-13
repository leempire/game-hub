import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const ts = require('typescript'), theme = require('../app/src/theme.ts');

function harness() {
  const slots = [], effects = [], nodes = new Map();
  let cursor = 0;
  const useState = initial => {
    const index = cursor++;
    if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
    return [slots[index], next => { slots[index] = typeof next === 'function' ? next(slots[index]) : next; }];
  };
  const React = {
    useState,
    useRef: initial => ({ current: initial }),
    useEffect(fn, deps) {
      const index = cursor++;
      if (!slots[index] || deps.some((value, i) => value !== slots[index][i])) {
        slots[index] = deps;
        effects.push(fn);
      }
    },
    createElement(type, props, ...children) { return { type, props: { ...props, children } }; },
  };
  const games = {
    GAMES: [
      { key: 'gomoku', title: '五子棋', short: '五子棋', category: 'board', ionicon: 'ellipse', Icon: () => null },
      { key: 'uno', title: 'UNO 卡牌', short: 'UNO', category: 'table', ionicon: 'albums', Icon: () => null },
      { key: 'stasis', title: '深空休眠', short: '深空休眠', category: 'coop', ionicon: 'planet', Icon: () => null },
    ],
    GAME_CATEGORIES: [
      { key: 'board', name: '棋类对战', blurb: '' },
      { key: 'table', name: '多人桌游', blurb: '' },
      { key: 'coop', name: '合作', blurb: '' },
    ],
  };
  const modules = {
    react: React,
    'react-native': {
      View: 'View', Text: 'Text', TouchableOpacity: 'Button', ScrollView: 'Scroll',
      StyleSheet: { create: value => value },
    },
    '@expo/vector-icons': { Ionicons: 'Icon' },
    './theme': theme,
    '../theme': theme,
    './ui': { AppModal: 'Modal', FormInput: 'Input' },
    './games': games,
  };
  function load(file) {
    const exports = {};
    runInNewContext(ts.transpileModule(readFileSync(new URL(`../app/src/${file}`, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true },
    }).outputText, {
      exports,
      require: key => { assert.ok(key in modules, key); return modules[key]; },
      Error,
    });
    return exports;
  }
  modules['./choice'] = load('choice.tsx');
  const roomForms = load('roomForms.tsx');
  const walk = node => {
    if (Array.isArray(node)) return node.forEach(walk);
    if (!node || typeof node !== 'object' || (node.type === 'Modal' && !node.props.visible)) return;
    // 函数组件在本 harness 里没有 React 渲染器，手动展开（它们都不用 hooks）。
    if (typeof node.type === 'function') return walk(node.type(node.props));
    if (node.props?.testID) nodes.set(node.props.testID, node.props);
    walk(node.props?.children);
  };
  const render = (component, props) => { cursor = 0; nodes.clear(); const tree = component(props); walk(tree); while (effects.length) effects.shift()(); return tree; };
  return {
    exports: { ...roomForms, ...modules['./choice'] },
    render, nodes, slots,
    get: id => { assert.ok(nodes.has(id), id); return nodes.get(id); },
    has: id => nodes.has(id),
    press: id => { render; get(id); nodes.get(id).onPress(); },
    text: () => JSON.stringify([...nodes.values()]),
  };
}

test('create-room game picker keeps 15 games collapsed behind one field', () => {
  const h = harness();
  const picks = [];
  const base = {
    visible: true, name: '', password: '', error: null, submitting: false,
    onPickGame: key => picks.push(key), onChangeName() {}, onChangePassword() {},
    onSubmit() {}, onClose() {},
  };
  const CreateRoomModal = h.exports.CreateRoomModal;
  h.render(CreateRoomModal, { ...base, game: 'gomoku' });
  // 默认只显示当前游戏的一行，不铺 15 个芯片。
  assert.ok(h.has('create-game-picker'));
  assert.equal(h.has('create-game-uno'), false);
  assert.equal(h.has('create-game-gomoku'), false);
  h.get('create-game-picker').onPress();
  h.render(CreateRoomModal, { ...base, game: 'gomoku' });
  assert.ok(h.has('create-game-uno'));
  assert.ok(h.has('create-game-stasis'));
  h.get('create-game-uno').onPress();
  h.render(CreateRoomModal, { ...base, game: 'uno' });
  assert.deepEqual(picks, ['uno']);
  // 选完自动收起
  assert.equal(h.has('create-game-stasis'), false);
});

test('filter sheet highlights every group’s current value at once', () => {
  const h = harness();
  const ChoiceModal = h.exports.ChoiceModal;
  h.render(ChoiceModal, {
    visible: true, title: '筛选条件', closeOnPick: false,
    groups: [
      { title: '时间范围', options: [{ key: 'all', label: '全部' }, { key: 'day', label: '今日' }] },
      { title: '扫雷难度', options: [{ key: 'beginner', label: '初级' }] },
    ],
    activeKeys: ['day', 'beginner'],
    onPick() {}, onClose() {},
  });
  assert.ok(h.has('choice-day'));
  assert.ok(h.has('choice-beginner'));
  assert.match(h.text(), /✓ .*今日/);
  assert.match(h.text(), /✓ .*初级/);
});