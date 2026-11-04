// Mount actual game screens with platform/network adapters, retaining authoritative WS callbacks.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
const appRoot = fileURLToPath(new URL('../app', import.meta.url));
const require = createRequire(path.join(appRoot, 'package.json'));
const React = require('react'), ts = require('typescript');
const { renderToStaticMarkup } = require('react-dom/server');

export function mountGame(name, initial, { game = name.toLowerCase(), os = 'web', width = 390, height = 844, room: customRoom } = {}) {
  const slots = [], effects = [], sockets = [], nodes = new Map(), handlers = [], notices = [], calls = [], navigationCalls = [], animations = [];
  let cursor = 0;
  const useState = initial => {
    const index = cursor++;
    if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
    return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
  };
  const memo = (factory, deps) => {
    const index = cursor++;
    if (!slots[index] || !deps || deps.some((value, i) => value !== slots[index].deps?.[i])) slots[index] = { deps, value: factory() };
    return slots[index].value;
  };
  const effect = (callback, deps) => {
    const index = cursor++;
    if (!slots[index] || !deps || deps.some((value, i) => value !== slots[index][i])) { slots[index] = deps; effects.push(callback); }
  };
  const hooks = { ...React, useState, useRef: initial => useState(() => ({ current: initial }))[0],
    useMemo: memo, useCallback: (callback, deps) => memo(() => callback, deps), useEffect: effect, useLayoutEffect: effect };
  const host = name => props => {
    if (props.testID) nodes.set(props.testID, props);
    if (props.panHarness) handlers.push(props.panHarness);
    return React.createElement(name, { 'data-testid': props.testID, 'aria-label': props.accessibilityLabel }, props.children);
  };
  class Value {
    constructor(value) { this.value = value; }
    setValue(value) { this.value = value; }
    interpolate() { return this.value; }
    stopAnimation() {} addListener() { return '0'; } removeListener() {}
    getTranslateTransform() { return [{ translateX: 0 }, { translateY: 0 }]; }
  }
  const animation = (value, config) => { if (config) animations.push(config); return { start() {}, stop() {} }; };
  const rn = { View: host('div'), Text: host('span'), TouchableOpacity: host('button'), Pressable: host('button'),
    ScrollView: host('section'), KeyboardAvoidingView: host('section'), ActivityIndicator: host('i'),
    Modal: ({ visible, children }) => visible ? children : null, Platform: { OS: os },
    StyleSheet: { create: value => value, absoluteFill: {}, absoluteFillObject: {} },
    Vibration: { vibrate() {} }, useWindowDimensions: () => ({ width, height }),
    PanResponder: { create: handler => ({ panHandlers: { panHarness: handler } }) },
    Animated: { Value, View: host('div'), Text: host('span'), timing: animation, spring: animation,
      sequence: animation, parallel: animation, loop: animation }, Easing: { linear: value => value },
  };
  class RoomSocket {
    isConnected = true; handlers = {};
    constructor() { sockets.push(this); }
    on(name, callback) { this.handlers[name] = callback; }
    connect(open) { open?.(); } close() {} send(value) { calls.push(value); }
    move(x, y) { calls.push(['move', x, y]); } drop(...args) { calls.push(['drop', ...args]); }
    xiangqiUndo() { calls.push(['undo']); } unoChallengeWild4() { calls.push(['challenge']); }
    unoCatch(target) { calls.push(['catch', target]); }
  }
  const room = customRoom ?? { game, status: 'playing', host_id: 1,
    players: [{ id: 1, username: 'human', connected: true, is_host: true }, { id: 2, username: 'opponent', connected: true }], settings: [] };
  const navigation = { addListener: () => () => {}, navigate: (...args) => navigationCalls.push(args), popTo: (...args) => navigationCalls.push(args), goBack() {}, setOptions() {} };
  const help = { GameHelpButton: () => null };
  const guards = { useModalBackGuard() {}, onConfirmLeave: () => () => {}, useWebKeys() {} };
  const theme = require(path.join(appRoot, 'src/theme.ts'));
  const adapters = {
    react: hooks, 'react-native': rn, '@expo/vector-icons': { Ionicons: host('i') },
    '@react-navigation/native': { useNavigation: () => navigation },
    'react-native-svg': { __esModule: true, default: host('svg'), ...Object.fromEntries(['Circle', 'Rect', 'Line', 'Path', 'Polygon', 'G', 'Text', 'Defs', 'Ellipse', 'ClipPath', 'LinearGradient', 'Stop'].map(name => [name, host('g')])) },
    auth: { useAuth: () => ({ token: 'test', user: { id: 1 } }) },
    api: { getRoom: async () => room, leaveRoom: async () => {} }, socket: { RoomSocket },
    help, backGuard: guards, disconnect: { useDisconnectCountdown: () => 120 },
    games: { gameTitle: value => value }, theme, utils: { notify: (...args) => notices.push(args) },
    icons: { PlaneGlyph: host('i'), DiceFace: host('i'), UnoColorBadge: host('i'), UnoSymbolGlyph: host('i') },
  };
  const cache = new Map();
  function load(file) {
    if (cache.has(file)) return cache.get(file);
    const exports = {}; cache.set(file, exports);
    const source = ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true,
    } }).outputText;
    runInNewContext(source, { exports, require: specifier => {
      if (specifier in adapters) return adapters[specifier];
      const base = path.basename(specifier);
      if (base in adapters) return adapters[base];
      assert.ok(specifier.startsWith('.'), `Unexpected dependency ${specifier}`);
      const target = ['.tsx', '.ts', ''].map(ext => path.resolve(path.dirname(file), specifier + ext)).find(existsSync);
      assert.ok(target, `Missing module ${specifier}`); return load(target);
    }, setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {}, Date });
    return exports;
  }
  const Component = load(path.join(appRoot, `src/screens/${name}Screen.tsx`)).default;
  const props = { code: 'TEST01', onLeave() {} };
  const view = {
    render() { cursor = 0; nodes.clear(); handlers.length = 0;
      const html = renderToStaticMarkup(Component(props)); for (const callback of effects.splice(0)) callback(); return html; },
    receive(type, message) { sockets[0].handlers[type]({ type, room, ...message }); return view.render(); },
    get: id => nodes.get(id), count: prefix => [...nodes.keys()].filter(key => key.startsWith(prefix)).length,
    press(id) { const node = nodes.get(id); assert.ok(node, `Missing ${id}`); if (!node.disabled) node.onPress(); return view.render(); },
    handlers, nodes, calls, notices, navigationCalls, animations,
  };
  view.render();
  if (initial) view.receive('sync', { game: initial });
  return view;
}
