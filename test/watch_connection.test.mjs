import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const ts = require('typescript');
function setup() {
  const sockets = [], intervals = new Map(), retries = new Map(), states = [], connected = [], errors = [], phrases = [];
  let timer = 0;
  class Socket {
    static OPEN = 1;
    readyState = 1; sent = [];
    constructor(url) { this.url = url; sockets.push(this); }
    send(value) { this.sent.push(JSON.parse(value)); }
    close() { this.closed = true; this.onclose?.({ code: 1000 }); }
  }
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(new URL('../app/src/watchConnection.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText, { exports, require: () => ({ WS_BASE: 'ws://example' }), WebSocket: Socket, Date,
    setInterval: fn => { intervals.set(++timer, fn); return timer; }, clearInterval: id => intervals.delete(id),
    setTimeout: fn => { retries.set(++timer, fn); return timer; }, clearTimeout: id => retries.delete(id) });
  const connection = new exports.default('private jwt', { ws_path: '/ws/watch/ABC', grant: 'private grant' },
    value => states.push(value), value => connected.push(value), value => errors.push(value), value => phrases.push(value));
  connection.connect();
  return { connection, sockets, intervals, retries, states, connected, errors, phrases };
}
test('spectator transport accepts only explicitly read-only states and sends only heartbeat', () => {
  const x = setup(), socket = x.sockets[0];
  assert.match(socket.url, /token=private%20jwt&grant=private%20grant/);
  socket.onopen();
  socket.onmessage({ data: JSON.stringify({ type: 'watch_state', read_only: false }) });
  assert.equal(x.states.length, 0);
  socket.onmessage({ data: JSON.stringify({ type: 'watch_sync', read_only: true, game: {} }) });
  socket.onmessage({ data: JSON.stringify({ type: 'quick_chat', text: 'hello' }) });
  assert.equal(x.states.length, 1); assert.equal(x.phrases.length, 1);
  for (const heartbeat of x.intervals.values()) heartbeat();
  assert.deepEqual(socket.sent, [{ type: 'ping' }]);
  x.connection.close();
  assert.equal(socket.closed, true); assert.equal(x.intervals.size, 0); assert.equal(x.retries.size, 0);
});
test('revoked spectator grant stops retry while a transient disconnect can reconnect', () => {
  const x = setup(); x.sockets[0].onopen();
  x.sockets[0].onclose({ code: 1006 });
  assert.equal(x.retries.size, 1);
  const callback = [...x.retries.values()][0]; x.retries.clear(); callback();
  const second = x.sockets[1]; second.onopen();
  second.onclose({ code: 4003, reason: '观战关闭' });
  assert.equal(x.retries.size, 0); assert.equal(x.errors.at(-1), '观战关闭');
  x.connection.close();
});
