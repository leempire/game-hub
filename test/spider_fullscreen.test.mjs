// node --test test/spider_fullscreen.test.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';

const require = createRequire(new URL('../app/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../app/src/spiderFullscreen.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup(os = 'web') {
  const listeners = new Map();
  const calls = [];
  const element = {};
  const document = { documentElement: element, fullscreenElement: null,
    addEventListener: (name, handler) => listeners.set(name, handler),
    removeEventListener: name => listeners.delete(name),
    exitFullscreen: async () => { calls.push('exit'); document.fullscreenElement = null; } };
  element.requestFullscreen = async () => { calls.push('enter'); document.fullscreenElement = element; };
  const orientation = { lock: async value => calls.push(value), unlock: () => calls.push('unlock') };
  const exports = {};
  runInNewContext(code, { exports, require: () => ({ Platform: { OS: os } }),
    document: os === 'web' ? document : undefined, window: os === 'web' ? { screen: { orientation } } : undefined });
  return { enter: exports.requestSpiderFullscreen, document, element, orientation, calls, listeners };
}

test('native path never reads DOM', () => {
  const env = setup('android');
  env.enter(() => assert.fail('unexpected exit'))();
  assert.deepEqual(env.calls, []);
});

test('unsupported browser leaves the component fallback usable', () => {
  const env = setup();
  delete env.element.requestFullscreen;
  env.enter(() => assert.fail('unexpected exit'))();
  assert.equal(env.listeners.size, 0);
  assert.deepEqual(env.calls, []);
});

test('requests fullscreen before landscape and releases both on exit', async () => {
  const env = setup();
  const stop = env.enter(() => {});
  assert.deepEqual(env.calls, ['enter']);
  await tick();
  assert.deepEqual(env.calls, ['enter', 'landscape']);
  stop();
  assert.deepEqual(env.calls, ['enter', 'landscape', 'unlock', 'exit']);
  assert.equal(env.listeners.size, 0);
});

test('browser fullscreen exit tells the component to restore its layout', async () => {
  const env = setup();
  let exits = 0;
  const stop = env.enter(() => { exits += 1; stop(); });
  await tick();
  env.document.fullscreenElement = null;
  env.listeners.get('fullscreenchange')();
  assert.equal(exits, 1);
  assert.equal(env.calls.at(-1), 'unlock');
  assert.equal(env.listeners.size, 0);
});

test('fullscreen and orientation rejection do not force the fallback to close', async () => {
  for (const fail of ['fullscreen', 'orientation']) {
    const env = setup();
    if (fail === 'fullscreen') env.element.requestFullscreen = () => Promise.reject(new Error('denied'));
    else env.orientation.lock = () => Promise.reject(new Error('not supported'));
    const stop = env.enter(() => assert.fail('fallback should remain open'));
    await tick();
    stop();
    assert.equal(env.listeners.size, 0);
    assert.ok(!env.calls.includes('unlock'));
  }
});

test('exit during a pending fullscreen request cleans up its eventual result', async () => {
  const env = setup();
  let finish;
  env.element.requestFullscreen = () => new Promise(resolve => { finish = resolve; });
  const stop = env.enter(() => {});
  stop();
  env.document.fullscreenElement = env.element;
  finish();
  await tick();
  assert.deepEqual(env.calls, ['exit']);
});

test('late orientation lock is released after leaving fullscreen', async () => {
  const env = setup();
  let finish;
  env.orientation.lock = () => new Promise(resolve => { finish = resolve; });
  const stop = env.enter(() => {});
  await tick();
  stop();
  finish();
  await tick();
  assert.deepEqual(env.calls, ['enter', 'exit', 'unlock']);
});

test('an old request cannot close a newly opened fullscreen session', async () => {
  const env = setup();
  const request = env.element.requestFullscreen;
  let finish;
  env.element.requestFullscreen = () => new Promise(resolve => { finish = resolve; });
  const stopOld = env.enter(() => {});
  stopOld();
  env.element.requestFullscreen = request;
  const stopNew = env.enter(() => {});
  await tick();
  finish();
  await tick();
  assert.ok(!env.calls.includes('exit'));
  stopNew();
  assert.deepEqual(env.calls, ['enter', 'landscape', 'unlock', 'exit']);
});
