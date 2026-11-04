import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
const require = createRequire(new URL('../app/package.json', import.meta.url)), ts = require('typescript');
const exports = {};
runInNewContext(ts.transpileModule(readFileSync(new URL('../app/src/chatTimeline.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports, require: () => ({}), Date, Math, Map, Set, Error });
const { ChatTimeline, mergeMessages } = exports;
const copy = value => JSON.parse(JSON.stringify(value));
const msg = (id, sender = 2, client = null) => ({ id, sender_id: sender, client_id: client, body: `text-${id}`, created_at: '2026-11-04T00:00:00Z' });
const page = (items, more = false, read = 0) => ({ peer: { id: 2, username: 'friend', nickname: null }, items, has_more: more, peer_read_through: read });
function make(overrides = {}) {
  const reads = [], queries = [], sent = [];
  let foreground = true;
  const api = {
    getChat: async (...args) => { queries.push(args[2]); return page([]); },
    sendMessage: async (...args) => { sent.push(args); return { ...msg(1, 1, args[3]), body: args[2] }; },
    markRead: async (...args) => { reads.push(args[2]); return { through_id: args[2] }; }, ...overrides,
  };
  const model = new ChatTimeline('token', 1, 2, () => foreground, api);
  model.setActive(true);
  return { model, reads, queries, sent, background() { foreground = false; model.invalidate(); },
    foreground() { foreground = true; model.invalidate(); } };
}
test('messages merge by stable server id and preserve ascending order', () => {
  assert.deepEqual(copy(mergeMessages([msg(5), msg(9)], [msg(3), { ...msg(5), body: 'updated' }])).map(m => [m.id, m.body]),
    [[3, 'text-3'], [5, 'updated'], [9, 'text-9']]);
});
test('incremental polling drains every cursor page without losing more than 100 messages', async () => {
  const queries = [];
  const h = make({ getChat: async (_token, _peer, cursor) => {
    queries.push(cursor);
    if (cursor.after === undefined) return page([msg(1)], false);
    if (cursor.after === 1) return page(Array.from({ length: 100 }, (_, i) => msg(i + 2)), true);
    if (cursor.after === 101) return page([msg(102), msg(103)], false, 103);
    throw new Error('unexpected cursor');
  } });
  await h.model.poll(() => true); await h.model.poll(() => true);
  assert.equal(h.model.state.messages.length, 103);
  assert.deepEqual(copy(queries), [{}, { after: 1, limit: 100 }, { after: 101, limit: 100 }]);
  assert.equal(h.model.state.peerReadThrough, 103);
  assert.equal(h.reads.at(-1), 103);
});
test('late background / blurred responses never append messages or send read receipts', async () => {
  let resolve;
  const h = make({ getChat: () => new Promise(r => { resolve = r; }) });
  const request = h.model.poll(() => true);
  h.background(); resolve(page([msg(7)])); await request;
  assert.equal(h.model.state.messages.length, 0); assert.equal(h.reads.length, 0);
  h.foreground();
  const second = h.model.poll(() => true); h.model.setActive(false); resolve(page([msg(9)])); await second;
  assert.equal(h.model.state.messages.length, 0); assert.equal(h.reads.length, 0);
});
test('retry retains draft and nonce, prevents parallel sends, and reconciles confirmed server delivery', async () => {
  const calls = []; let reject;
  const h = make({ getChat: async () => page([]), sendMessage: (...args) => {
    calls.push(args); return new Promise((_resolve, fail) => { reject = fail; });
  } });
  await h.model.poll(() => true); h.model.changeDraft('hello');
  const first = h.model.send(); await h.model.send();
  assert.equal(calls.length, 1); reject(new Error('timeout')); await first;
  assert.equal(h.model.state.draft, 'hello'); assert.match(h.model.state.sendError, /内容已保留/);
  const retry = h.model.send(); assert.equal(calls.length, 2);
  assert.equal(calls[0][3], calls[1][3]); reject(new Error('timeout')); await retry;
  h.model['api'].getChat = async () => page([{ ...msg(8, 1, calls[0][3]), body: 'hello' }]);
  await h.model.poll(() => true);
  assert.equal(h.model.state.draft, ''); assert.equal(h.model.state.sendError, '');
  await h.model.send(); assert.equal(calls.length, 2);
});
test('loading older messages preserves incoming updates and does not send unread receipts while scrolled away', async () => {
  let earlier;
  const h = make({ getChat: async (_token, _peer, cursor) => {
    if (cursor.before) return new Promise(resolve => { earlier = resolve; });
    return cursor.after ? page([msg(11)]) : page([msg(10)], true);
  } });
  await h.model.poll(() => true); assert.deepEqual(h.reads, [10]);
  const loading = h.model.loadOlder(); await h.model.poll(() => true);
  earlier(page([msg(3), msg(4)])); await loading;
  assert.deepEqual(copy(h.model.state.messages).map(m => m.id), [3, 4, 10, 11]);
  assert.deepEqual(h.reads, [10]);
  h.model.setAtBottom(true); await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(h.reads, [10, 11]);
});
test('read cursor uses received messages only and external stale guard stops pagination', async () => {
  const h = make({ getChat: async () => page([msg(4), msg(9, 1)]) });
  await h.model.poll(() => true); assert.deepEqual(h.reads, [4]);
  let valid = true;
  h.model['api'].getChat = async () => { valid = false; return page([msg(10)], true); };
  await h.model.poll(() => valid);
  assert.deepEqual(copy(h.model.state.messages).map(m => m.id), [4, 9]);
  assert.deepEqual(h.reads, [4]);
});
test('changing a failed draft creates a new send identity and old receipt cannot clear new text', async () => {
  const calls = [];
  const h = make({ sendMessage: async (...args) => { calls.push(args); throw new Error('offline'); } });
  await h.model.poll(() => true); h.model.changeDraft('first'); await h.model.send();
  h.model.changeDraft('second'); await h.model.send();
  assert.notEqual(calls[0][3], calls[1][3]);
  h.model['api'].getChat = async () => page([{ ...msg(10, 1, calls[0][3]), body: 'first' }]);
  await h.model.poll(() => true); assert.equal(h.model.state.draft, 'second');
});

const scrolling = {};
runInNewContext(ts.transpileModule(readFileSync(new URL('../app/src/chatScroll.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { exports: scrolling });
test('new layout preserves bottom-follow before and after content-size callback ordering', () => {
  for (const order of ['content-first', 'scroll-first']) {
    const anchor = new scrolling.BottomAnchor();
    anchor.observe(500, 1000, 500);
    if (order === 'content-first') anchor.resized(4000);
    assert.deepEqual(copy(anchor.observe(500, 4000, 500)), { bottom: true, snap: true });
    assert.equal(anchor.following, true);
    assert.deepEqual(copy(anchor.observe(3500, 4000, 500)), { bottom: true, snap: false });
    assert.deepEqual(copy(anchor.observe(3000, 4000, 500)), { bottom: false, snap: false });
    assert.equal(anchor.resized(4500), false);
    anchor.follow(); assert.equal(anchor.resized(5000), true);
  }
});
test('user dragging or loading history interrupts pending layout-follow', () => {
  const anchor = new scrolling.BottomAnchor();
  anchor.resized(1000); anchor.beginDrag();
  assert.deepEqual(copy(anchor.observe(100, 4000, 500)), { bottom: false, snap: false });
  assert.equal(anchor.resized(5000), false); anchor.endDrag();
  anchor.follow(); anchor.pause();
  assert.equal(anchor.resized(6000), false);
});

test('keyboard viewport shrink follows latest only when the user was at the bottom', () => {
  const anchor = new scrolling.BottomAnchor();
  anchor.observe(500, 1000, 500);
  assert.deepEqual(copy(anchor.observe(500, 1000, 250)), { bottom: true, snap: true });
  anchor.observe(750, 1000, 250);
  anchor.pause();
  assert.deepEqual(copy(anchor.observe(100, 1000, 200)), { bottom: false, snap: false });
});
test('send committed after blur is reconciled on return, without rendering into the old focus', async () => {
  let resolve, clientId;
  const h = make({ sendMessage: (_token, _peer, _body, id) => {
    clientId = id; return new Promise(done => { resolve = done; });
  } });
  await h.model.poll(() => true); h.model.changeDraft('hello');
  let notifications = 0;
  h.model.subscribe(() => notifications++);
  const sending = h.model.send(); h.model.setActive(false);
  const before = notifications;
  resolve({ ...msg(50, 1, clientId), body: 'hello' }); await sending;
  assert.equal(notifications, before); assert.equal(h.model.state.messages.length, 0);
  assert.equal(h.model.state.draft, 'hello');
  h.model['api'].getChat = async () => page([{ ...msg(50, 1, clientId), body: 'hello' }]);
  h.model.setActive(true); await h.model.poll(() => true);
  assert.equal(h.model.state.messages.length, 1); assert.equal(h.model.state.draft, '');
});

test('latest scroll uses measured ScrollView extent and only falls back to estimated list frames when unavailable', () => {
  const calls = [];
  scrolling.scrollToLatest({ getNativeScrollRef: () => ({ scrollToEnd: options => calls.push(['actual', options.animated]) }),
    scrollToEnd: () => calls.push(['estimate']) });
  assert.deepEqual(calls, [['actual', false]]);
  scrolling.scrollToLatest({ getNativeScrollRef: () => null, scrollToEnd: options => calls.push(['fallback', options.animated]) });
  assert.deepEqual(calls.at(-1), ['fallback', false]);
});
