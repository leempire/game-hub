import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const ts = require('typescript'), exports = {};
const code = ts.transpileModule(readFileSync(new URL('../app/src/deepLink.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
runInNewContext(code, { exports, URL, require: name => name === 'react-native'
  ? { Platform: { OS: 'android' } } : { getInitialURL: async () => null } });
const open = exports.destinationAlreadyOpen;
test('late initial room link never reopens a room after any of its games is already on top', () => {
  const target = { screen: 'Room', params: { code: 'ABC123' } };
  for (const name of ['Room', 'Gomoku', 'Xiangqi', 'Dots', 'Uno', 'Stasis', 'UltimateTicTacToe', 'Spider'])
    assert.equal(open(target, { name, params: { code: 'ABC123' } }), true, name);
});
test('a different room, profile or read-only spectator never consumes a player room link', () => {
  const target = { screen: 'Room', params: { code: 'ABC123' } };
  assert.equal(open(target, { name: 'Dots', params: { code: 'OTHER1' } }), false);
  assert.equal(open(target, { name: 'Profile' }), false);
  assert.equal(open(target, { name: 'Watch', params: { code: 'ABC123' } }), false);
  assert.equal(open(target, undefined), false);
});

test('chat startup links preserve numeric user IDs and never match unrelated rooms', async () => {
  const load = path => {
    const result = {};
    runInNewContext(code, { exports: result, URL, Number, window: { location: { pathname: path } },
      require: name => name === 'react-native' ? { Platform: { OS: 'web' } } : { getInitialURL: async () => null } });
    return result;
  };
  const chat = load('/chat/42');
  const route = await chat.takePendingRoute();
  assert.equal(route.screen, 'Chat'); assert.equal(route.params.userId, 42);
  assert.equal(chat.destinationAlreadyOpen(route, { name: 'Chat', params: { userId: 42 } }), true);
  assert.equal(chat.destinationAlreadyOpen(route, { name: 'Chat', params: { userId: 43 } }), false);
  assert.equal(chat.destinationAlreadyOpen(route, { name: 'Room', params: { code: 'ABC123' } }), false);
  for (const invalid of ['/chat/0', '/chat/-2', '/chat/NaN', '/chat/1.5', '/chat/9007199254740992'])
    assert.equal(await load(invalid).takePendingRoute(), null, invalid);
});
