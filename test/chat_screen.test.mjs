import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import test from 'node:test';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const React = require('react'), ts = require('typescript');
const { renderToStaticMarkup } = require('react-dom/server');
function harness() {
  const slots = [], effects = [], elements = new Map(), sent = [], reads = [];
  let cursor = 0, poll, focused = true, active = true, html = '', rejectSend = false, feedError = false;
  const messages = [{ id: 1, sender_id: 2, client_id: null, body: '<script>hello</script>', created_at: '2026-11-04T00:00:00Z' }];
  const state = initial => { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
    return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; };
  const hooks = { ...React, useState: state, useRef: initial => state(() => ({ current: initial }))[0], useCallback: fn => fn,
    useEffect(effect, deps) { const i = cursor++, prior = slots[i];
      if (!prior || deps.some((value, j) => value !== prior.deps[j])) effects.push(() => { prior?.cleanup?.(); slots[i] = { deps, cleanup: effect() }; }); } };
  const host = name => props => { if (props.testID) elements.set(props.testID, props); return React.createElement(name, null, props.children); };
  const contacts = { displayContact: user => user.nickname || user.username,
    getChat: async () => { if (feedError) throw Error('成为好友后才能聊天'); return { peer: { id: 2, username: 'friend', nickname: null },
      items: messages, has_more: false, peer_read_through: 100 }; },
    markRead: async (_token, _peer, through) => { reads.push(through); return { through_id: through }; },
    sendMessage: async (_token, _peer, body, clientId) => {
      sent.push({ body, clientId }); if (rejectSend) throw Error('offline');
      const message = { id: 2, sender_id: 1, client_id: clientId, body, created_at: '2026-11-04T00:01:00Z' };
      messages.push(message); return message;
    } };
  const modules = { react: hooks,
    'react-native': { View: host('div'), Text: host('span'), TextInput: host('textarea'), TouchableOpacity: host('button'),
      KeyboardAvoidingView: host('article'), ActivityIndicator: () => null, Platform: { OS: 'android' },
      FlatList: props => React.createElement('main', null, props.ListHeaderComponent,
        props.data.length ? props.data.map(item => React.createElement(React.Fragment, { key: item.id }, props.renderItem({ item }))) : props.ListEmptyComponent),
      AppState: { get currentState() { return active ? 'active' : 'background'; }, addEventListener: () => ({ remove() {} }) },
      StyleSheet: { create: value => value } },
    '@react-navigation/native': { useNavigation: () => ({ setOptions() {} }), useFocusEffect: callback => hooks.useEffect(() => focused ? callback() : undefined, [focused]) },
    'react-native-safe-area-context': { useSafeAreaInsets: () => ({ bottom: 0, top: 20 }) },
    '../auth': { useAuth: () => ({ token: 'token', user: { id: 1 } }) }, '../contacts': contacts,
    '../theme': require('../app/src/theme.ts'), '../ui': { Card: host('section') },
    '../useForegroundPolling': { __esModule: true, default: (_key, task, onError) => {
      poll = async () => { try { await task(() => active && focused); } catch (error) { if (active && focused) onError(error); } };
      return () => void poll();
    } },
  };
  function load(file) {
    const exports = {};
    runInNewContext(ts.transpileModule(readFileSync(new URL(`../app/src/${file}`, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true },
    }).outputText, { exports, require: name => { if (name === './contacts') return contacts; assert.ok(name in modules, name); return modules[name]; }, Date, Math, Map, Set, Error });
    return exports;
  }
  modules['../chatTimeline'] = load('chatTimeline.ts');
  modules['../chatScroll'] = load('chatScroll.ts');
  const screen = load('screens/ChatScreen.tsx').default;
  function render() { cursor = 0; elements.clear(); html = renderToStaticMarkup(React.createElement(screen, { route: { params: { userId: 2 } } })); effects.splice(0).forEach(fn => fn()); }
  return { render, get: id => { render(); assert.ok(elements.has(id), id); return elements.get(id); },
    press(id) { const element = this.get(id); if (!element.disabled) element.onPress(); },
    type(value) { this.get('chat-input').onChangeText(value); render(); },
    async poll() { render(); await poll(); render(); }, async settle() { await new Promise(resolve => setImmediate(resolve)); render(); },
    reject(value) { rejectSend = value; }, feedError(value) { feedError = value; },
    focus(value) { focused = value; render(); }, foreground(value) { active = value; },
    sent, reads, get html() { return html; }, unmount() { slots.forEach(slot => slot?.cleanup?.()); } };
}
test('chat screen wires draft, send, plain text message bubbles and read receipt display', async () => {
  const h = harness(); h.render(); assert.equal(h.get('chat-send').disabled, true);
  await h.poll(); assert.ok(h.get('chat-ready')); assert.ok(h.get('chat-message-1'));
  assert.match(h.html, /&lt;script&gt;hello&lt;\/script&gt;/); assert.doesNotMatch(h.html, /<script>/);
  h.type('你好'); assert.equal(h.get('chat-send').disabled, false); h.press('chat-send'); await h.settle();
  assert.equal(h.sent.length, 1); assert.equal(h.get('chat-input').value, '');
  assert.ok(h.get('chat-message-2')); assert.match(h.html, /已读/); assert.deepEqual(h.reads, [1]);
  h.unmount();
});
test('failed UI send preserves text for same-identity retry and revoked friendship disables composer', async () => {
  const h = harness(); await h.poll(); h.reject(true); h.type('请重试');
  h.press('chat-send'); await h.settle(); assert.equal(h.get('chat-input').value, '请重试'); assert.ok(h.get('chat-send-error'));
  h.press('chat-send'); await h.settle(); assert.equal(h.sent[0].clientId, h.sent[1].clientId);
  h.feedError(true); await h.poll(); assert.ok(h.get('chat-error')); assert.equal(h.get('chat-send').disabled, true);
  h.unmount();
});
