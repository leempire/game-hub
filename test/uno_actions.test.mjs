// node --test test/uno_actions.test.mjs（先安装 app/ 的依赖）
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
import { cardKind } from '../app/src/uno.ts';

const require = createRequire(new URL('../app/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../app/src/screens/UnoScreen.tsx', import.meta.url), 'utf8');
// 执行生产代码中的整段出牌/选色/选人回调，避免在测试里重写一套点击逻辑。
const start = source.indexOf('  const onCardPress =');
const end = source.indexOf('  const doSurrender =', start);
assert.ok(start >= 0 && end > start);
const callbacks = ts.transpileModule(source.slice(start, end), {}).outputText;

function screen({ turn = false, card = 'R7', targets = [0, 2], allowed = true } = {}) {
  const sent = [];
  const state = {
    cardKind,
    isMyTurn: turn,
    playableSet: new Set(turn && allowed ? [card] : []),
    jumpableSet: new Set(!turn && allowed ? [card] : []),
    colorKinds: new Set(['']), swapTargets: targets,
    rouletteYours: false, pickTargetFor: null, pickColorFor: null,
    socketRef: { current: { unoPlay: (...args) => sent.push(args) } },
    setPickTargetFor: (value) => { state.pickTargetFor = value; },
    setPickColorFor: (value) => { state.pickColorFor = value; },
  };
  const handlers = runInNewContext(callbacks + '\n({ onCardPress, onPickTarget, onPickColor })', state);
  return { ...handlers, state, sent };
}

for (const turn of [false, true]) {
  test(`${turn ? '正常打出' : '抢出'} 7：先选对象，再携带正确的 jump/target 发送`, () => {
    const view = screen({ turn });
    view.onCardPress('R7');
    assert.equal(view.sent.length, 0);
    assert.ok(view.state.pickTargetFor);
    view.onPickTarget(2);
    assert.deepEqual(view.sent, [['R7', undefined, !turn, 2]]);
    assert.equal(view.state.pickTargetFor, null);
  });
}

test('关闭换手时，抢出的 7 直接发送', () => {
  const view = screen({ targets: [] });
  view.onCardPress('R7');
  assert.deepEqual(view.sent, [['R7', undefined, true]]);
  assert.equal(view.state.pickTargetFor, null);
});

test('普通数字牌仍可直接抢出', () => {
  const view = screen({ card: 'R5' });
  view.onCardPress('R5');
  assert.deepEqual(view.sent, [['R5', undefined, true]]);
});

for (const turn of [false, true]) {
  test(`${turn ? '本回合' : '非本回合'}不能出的牌不会发送动作或打开选人`, () => {
    const view = screen({ turn, allowed: false });
    view.onCardPress('R7');
    assert.equal(view.sent.length, 0);
    assert.equal(view.state.pickTargetFor, null);
  });
}

test('黑牌仍先选色再发送', () => {
  const view = screen({ turn: true, card: 'W' });
  view.onCardPress('W');
  assert.equal(view.sent.length, 0);
  assert.equal(view.state.pickColorFor, 'W');
  view.onPickColor('G');
  assert.deepEqual(view.sent, [['W', 'G']]);
});
