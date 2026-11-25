import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import { Animated, Easing, Image, Platform, StyleSheet, Text, View } from 'react-native';
import type { FishDirection, FishState } from './fish';
import { fishColors as c } from './theme';

const reef = require('../assets/fish/reef.webp');
const sprites = {
  self: [require('../assets/fish/self-0.webp'), require('../assets/fish/self-1.webp')],
  peer: [require('../assets/fish/peer-0.webp'), require('../assets/fish/peer-1.webp')],
  prey: [require('../assets/fish/prey-0.webp'), require('../assets/fish/prey-1.webp')],
  neutral: [require('../assets/fish/neutral-0.webp'), require('../assets/fish/neutral-1.webp')],
  predator: [require('../assets/fish/predator-0.webp'), require('../assets/fish/predator-1.webp')],
  apex: [require('../assets/fish/apex-0.webp'), require('../assets/fish/apex-1.webp')],
};
type Kind = keyof typeof sprites;
const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const nativeDriver = Platform.OS !== 'web';
const cameraOffset = (position: number, view: number, world: number) =>
  Math.min(0, world - view) / 2 + clamp(position - view / 2, 0, world - view);

const Swimmer = memo(function Swimmer({ x, y, r, facing, kind, phase, name, shield, immune, position, live }: {
  x: number; y: number; r: number; facing: number; kind: Kind; phase: number;
  name?: string; shield?: boolean; immune?: boolean; live: boolean;
  position?: { x: Animated.Value; y: Animated.Value; facing: Animated.Value };
}) {
  const [motion] = useState(() => ({ x: new Animated.Value(x), y: new Animated.Value(y) }));
  useEffect(() => {
    if (position) return;
    const tween = Animated.parallel([
      Animated.timing(motion.x, { toValue: x, duration: live ? 105 : 0, easing: Easing.linear, useNativeDriver: nativeDriver, isInteraction: false }),
      Animated.timing(motion.y, { toValue: y, duration: live ? 105 : 0, easing: Easing.linear, useNativeDriver: nativeDriver, isInteraction: false }),
    ]);
    tween.start();
    return () => tween.stop();
  }, [live, motion, position, x, y]);
  const xy = position ?? motion;
  const isPlayer = kind === 'self' || kind === 'peer';
  return <Animated.View testID={kind === 'self' ? 'fish-player-self' : undefined} style={[styles.entity, { transform: [{ translateX: xy.x }, { translateY: xy.y }] }]}>
    {(kind === 'predator' || kind === 'apex') && <View style={[styles.halo, { left: -r, top: -r, width: 2 * r, height: 2 * r, borderColor: c.coral, borderStyle: kind === 'apex' ? 'dashed' : 'solid' }]} />}
    {(immune || shield || kind === 'self') && <View style={[styles.halo, { left: -r - 4, top: -r - 4, width: 2 * r + 8, height: 2 * r + 8, borderColor: shield && !immune ? c.amber : c.cyan, opacity: immune || shield ? 1 : .5 }]} />}
    <Animated.Image source={sprites[kind][phase]} fadeDuration={0}
      style={{ position: 'absolute', left: -1.65 * r, top: -r, width: 3.3 * r, height: 2 * r, transform: [{ scaleX: position?.facing ?? facing }] }} />
    {isPlayer && <Text numberOfLines={1} style={[styles.fishName, { top: r + 1, color: kind === 'self' ? c.cyan : c.peer }]}>{name}</Text>}
  </Animated.View>;
});

const FishSea = memo(function FishSea({ game, uid, width, height, input, live, watchId }: {
  game: FishState | null; uid?: number; width: number; height: number;
  input: React.RefObject<FishDirection>; live: boolean; watchId?: number;
}) {
  const me = game?.players.find(p => p.id === uid && p.lives);
  const follow = me ?? game?.players.find(p => p.id === watchId && p.lives) ?? game?.players.find(p => p.lives);
  const [motion] = useState(() => ({
    local: { x: new Animated.Value(0), y: new Animated.Value(0), facing: new Animated.Value(1) },
    camera: { x: new Animated.Value(0), y: new Animated.Value(0) },
  }));
  const latest = useRef({ game, me, follow });
  useEffect(() => { latest.current = { game, me, follow }; }, [game, me, follow]);
  const position = useRef({ id: undefined as number | undefined, x: 0, y: 0, frame: -1, lives: 0, seen: 0, facing: 1 });
  const baseScale = clamp(width / 520, .75, 1);
  // Pull the camera back as a fish grows so the approach lanes remain visible.
  const scale = baseScale / (1 + .007 * Math.max(0, (follow?.r ?? 14) - 14));
  const viewW = width / scale;
  const viewH = height / scale;

  const tracking = !!follow && width > 0 && height > 0;
  useEffect(() => {
    if (!tracking) return;
    const p = position.current;
    let previous = performance.now();
    let handle: number;
    let renderedX = NaN;
    let renderedY = NaN;
    const draw = (now: number) => {
      const state = latest.current;
      if (!state.game || !state.follow) return;
      const target = state.follow;
      const dt = clamp((now - previous) / 1000, 0, .05);
      previous = now;
      const frame = state.game.frame;
      if (p.id !== target.id || frame < p.frame || p.lives !== target.lives) {
        Object.assign(p, { id: target.id, x: target.x, y: target.y, lives: target.lives });
      }
      if (frame !== p.frame) {
        p.frame = frame;
        p.seen = now;
      }
      const speed = target.speed ?? (184 - target.r) * (target.boost ? 1.5 : 1);
      const [dx, dy] = state.me && live && now - p.seen < 300 ? input.current : [0, 0];
      const facing = Math.abs(dx) > .05 ? Math.sign(dx) : target.facing;
      if (p.facing !== facing) {
        p.facing = facing;
        motion.local.facing.setValue(facing);
      }
      const correction = 1 - Math.exp(-dt * 14);
      const drift = Math.min(.15, .045 + (now - p.seen) / 1000);
      p.x = clamp(p.x + dx * speed * dt + (target.x + dx * speed * drift - p.x) * correction,
        target.r, state.game.width - target.r);
      p.y = clamp(p.y + dy * speed * dt + (target.y + dy * speed * drift - p.y) * correction,
        target.r, state.game.height - target.r);
      const x = Math.round(p.x * scale * 10) / 10;
      const y = Math.round(p.y * scale * 10) / 10;
      if (renderedX !== x) {
        renderedX = x;
        motion.local.x.setValue(x);
        motion.camera.x.setValue(-cameraOffset(p.x, viewW, state.game.width) * scale);
      }
      if (renderedY !== y) {
        renderedY = y;
        motion.local.y.setValue(y);
        motion.camera.y.setValue(-cameraOffset(p.y, viewH, state.game.height) * scale);
      }
      if (live) handle = requestAnimationFrame(draw);
    };
    draw(previous);
    return () => cancelAnimationFrame(handle);
  }, [input, live, motion, scale, tracking, viewH, viewW]);

  const worldWidth = game?.width ?? 0;
  const worldHeight = game?.height ?? 0;
  const walls = useMemo(() => {
    if (!worldWidth || !worldHeight) return [];
    // Sparse bubbles and map edges give the camera a readable sense of motion.
    return Array.from({ length: 36 }, (_, i) => ({
      key: i, x: (37 + i * 227) % worldWidth, y: (71 + i * 173) % worldHeight, r: 2 + i % 4,
    }));
  }, [worldWidth, worldHeight]);
  const focusX = follow?.x ?? (game?.width ?? 0) / 2;
  const focusY = follow?.y ?? (game?.height ?? 0) / 2;
  const left = cameraOffset(focusX, viewW, worldWidth);
  const top = cameraOffset(focusY, viewH, worldHeight);
  const visible = (x: number, y: number) =>
    x >= left - 120 && x <= left + viewW + 120 && y >= top - 120 && y <= top + viewH + 120;
  const size = me?.r ?? follow?.r ?? 14;
  const phase = Math.floor((game?.frame ?? 0) / 6) % 2;

  return <View style={StyleSheet.absoluteFill} pointerEvents="none">
    <Image source={reef} resizeMode="cover" style={StyleSheet.absoluteFill} />
    {game?.event === 'current' && <View style={[StyleSheet.absoluteFill, { backgroundColor: c.currentTint }]} />}
    {game?.event === 'school' && <View style={[StyleSheet.absoluteFill, { backgroundColor: c.schoolTint }]} />}
    {game && <Animated.View style={[styles.map, {
      width: game.width * scale, height: game.height * scale,
      transform: [{ translateX: motion.camera.x }, { translateY: motion.camera.y }],
    }]}>
      {walls.filter(b => visible(b.x, b.y)).map(b =>
        <View key={b.key} style={[styles.bubble, { left: b.x * scale, top: b.y * scale, width: b.r, height: b.r }]} />)}
      {game.fish.filter(([, x, y]) => visible(x, y)).map(([id, x, y, r, facing]) =>
        <Swimmer key={id} x={x * scale} y={y * scale} r={r * scale} facing={facing} phase={(phase + id) % 2} live={live}
          kind={r > size * 1.08 ? (r >= 73 ? 'apex' : 'predator') : r < size * .92 ? 'prey' : 'neutral'} />)}
      {game.items.filter(([, x, y]) => visible(x, y)).map(([id, x, y, kind]) =>
        <View key={id} style={[styles.orb, { left: x * scale - 11, top: y * scale - 11, borderColor: kind === 'boost' ? c.green : c.amber }]}>
          <Text style={{ color: kind === 'boost' ? c.green : c.amber, fontWeight: '800' }}>{kind === 'boost' ? '↟' : '◇'}</Text>
        </View>)}
      {game.players.filter(p => p.lives > 0 && visible(p.x, p.y)).map(p =>
        <Swimmer key={`${p.id}:${p.lives}`} x={p.x * scale} y={p.y * scale} r={p.r * scale} facing={p.facing}
          name={p.id === uid ? '你' : p.name} kind={p.id === uid ? 'self' : 'peer'}
          shield={p.shield} immune={p.immune > 0} phase={phase} live={live}
          position={p.id === uid ? motion.local : undefined} />)}
    </Animated.View>}
  </View>;
});

export default FishSea;

const styles = StyleSheet.create({
  map: { position: 'absolute', top: 0, left: 0, borderWidth: 1, borderColor: c.line },
  entity: { position: 'absolute', left: 0, top: 0 },
  fishName: { position: 'absolute', width: 110, left: -55, textAlign: 'center', fontSize: 12, fontWeight: '700',
    textShadowColor: c.deep, textShadowRadius: 4 },
  halo: { position: 'absolute', borderRadius: 100, borderWidth: 1.5, opacity: .75 },
  bubble: { position: 'absolute', borderColor: c.cyan, borderWidth: 1, borderRadius: 8, opacity: .26 },
  orb: { position: 'absolute', width: 22, height: 22, borderWidth: 1.5, borderRadius: 11,
    backgroundColor: c.glass, alignItems: 'center', justifyContent: 'center' },
});
