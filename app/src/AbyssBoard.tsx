import React, { useEffect, useRef, useState } from 'react';
import { Image, StyleSheet, View } from 'react-native';
import Svg, {
  Circle, Defs, Ellipse, G, Line, Path, Polygon, Polyline,
  RadialGradient, Rect, Stop, Text,
} from 'react-native-svg';
import { abyssWorld, type AbyssEnemy, type AbyssState, type AbyssWeapon, type XY } from './abyss';
import { abyssColors as c } from './theme';

const floorImage = require('../assets/abyss/recovery-floor.png');
const radians = 180 / Math.PI;

const weaponColor = (weapon: string) =>
  (c as unknown as Record<string, string>)[weapon] ?? c.signal;

function Diver({ mine, down }: { mine: boolean; down: boolean }) {
  return <G opacity={down ? .58 : 1}>
    <Ellipse cx="0" cy="4" rx="23" ry="17" fill={c.shadow} opacity=".8" />
    <Path d="M-6-9L-17-12L-21-9M-6 9L-17 12L-21 9" stroke={c.edge} strokeWidth="5" fill="none" />
    <Line x1="-12" y1="-5" x2="-12" y2="5" stroke={c.brass} strokeWidth="11" />
    <Ellipse rx="16" ry="14" fill={c.suit} stroke={c.shadow} strokeWidth="2" />
    <Ellipse cx="4" rx="13" ry="12" fill={c.edge} stroke={c.brass} strokeWidth="2" />
    <Circle cx="8" r="8" fill={c.visor} stroke={mine ? c.signal : c.beacon} strokeWidth="2" />
    <Path d="M8-5q5 0 5 6" fill="none" stroke={c.text} strokeWidth="2" />
    <Path d="M9 12l9-3h4" fill="none" stroke={c.brass} strokeWidth="4" />
  </G>;
}

function Creature({ kind }: { kind: AbyssEnemy }) {
  const color = c[kind];
  if (kind === 'drifter') return <G>
    <Path d="M-3-6q-9-7-17 3M-3 0q-9-7-17 3M-3 6q-9-7-17 3" stroke={color} strokeWidth="2" fill="none" />
    <Ellipse rx="12" ry="11" fill={color} stroke={c.signal} />
    <Ellipse cx="4" rx="5" ry="7" fill={c.visor} /><Circle cx="7" cy="-2" r="2" fill={c.text} />
  </G>;
  if (kind === 'eel') return <G>
    <Path d="M-25 6C-13-16-10 12 10 0" stroke={color} strokeWidth="8" fill="none" strokeLinecap="round" />
    <Ellipse cx="7" rx="12" ry="7" fill={color} stroke={c.shadow} />
    <Circle cx="13" cy="-2" r="2" fill={c.text} />
  </G>;
  return <G scale={kind === 'crab' ? 1 : kind === 'elite' ? 1.35 : 1.58}>
    <Path d="M4 6l-6 8-8 4M-2 6l-7 8-8 4M-8 6l-8 8-8 4M4-6l-6-8-8-4M-2-6l-7-8-8-4M-8-6l-8-8-8-4" fill="none" stroke={color} strokeWidth="3" />
    <Path d="M7 7l8 6 6-4M7-7l8-6 6 4" fill="none" stroke={color} strokeWidth="5" />
    <Ellipse rx="15" ry="12" fill={color} stroke={c.shadow} />
    <Path d="M-11-5L-1-8L8-4" fill="none" stroke={c.brass} strokeWidth="2" />
    <Circle cx="10" cy="-4" r="2" fill={c.text} /><Circle cx="10" cy="4" r="2" fill={c.text} />
    {kind === 'warden' && <Path d="M-9 8L0 0L-9-8" fill="none" stroke={c.text} strokeWidth="2" />}
  </G>;
}

function Shot({ weapon, x, y, angle, r }: {
  weapon: AbyssWeapon; x: number; y: number; angle: number; r: number;
}) {
  const color = weaponColor(weapon);
  const rotate = angle * radians;
  if (weapon === 'harpoon' || weapon === 'thousand_edge') {
    return <G transform={`translate(${x} ${y}) rotate(${rotate})`}>
      <Line x1="-16" y1="0" x2="16" y2="0" stroke={color} strokeWidth="4" strokeLinecap="round" />
      <Line x1="-9" y1="0" x2="9" y2="0" stroke={c.text} strokeWidth="1" />
    </G>;
  }
  if (weapon === 'axe' || weapon === 'death_spiral') {
    return <G transform={`translate(${x} ${y}) rotate(${rotate})`}>
      <Path d="M-10-10Q0-19 13-8L4 0L13 8Q0 19 -10 10Z"
        fill={color} stroke={c.shadow} strokeWidth="1.5" />
    </G>;
  }
  if (weapon === 'cross' || weapon === 'heaven_sword') {
    return <G transform={`translate(${x} ${y}) rotate(${rotate})`}>
      <Path d="M-3-13H3V-3H13V3H3V13H-3V3H-13V-3H-3Z"
        fill={color} stroke={c.shadow} strokeWidth="1.5" />
    </G>;
  }
  if (weapon === 'charge' || weapon === 'hellfire') {
    return <G transform={`translate(${x} ${y})`}>
      <Circle r={r + 5} fill={color} opacity=".25" />
      <Circle r={r} fill={color} stroke={c.text} strokeWidth="1.5" />
    </G>;
  }
  if (weapon === 'rune' || weapon === 'no_future') {
    return <G transform={`translate(${x} ${y}) rotate(${rotate})`}>
      <Circle r={r} fill="none" stroke={color} strokeWidth="3" />
      <Circle r={r * .35} fill={color} />
    </G>;
  }
  return <G transform={`translate(${x} ${y}) rotate(${rotate})`}>
    <Line x1="-11" y1="0" x2="11" y2="0" stroke={color} strokeWidth="4" strokeLinecap="round" />
    <Circle r="3" fill={c.text} />
  </G>;
}

function Chest({ x, y }: { x: number; y: number }) {
  return <G transform={`translate(${x} ${y})`}>
    <Rect x="-10" y="-7" width="20" height="15" rx="2" fill={c.chest} stroke={c.shadow} strokeWidth="1.5" />
    <Rect x="-10" y="-7" width="20" height="5" rx="2" fill={c.brass} />
    <Rect x="-2" y="-1" width="4" height="7" rx="1" fill={c.deep} />
  </G>;
}

function Effect({ effect, fade }: { effect: AbyssState['effects'][number]; fade: number }) {
  const color = weaponColor(effect.weapon);
  const kind = effect.kind ?? 'ring';
  if (effect.points) {
    const points = effect.points.map(([x, y]) => `${x},${y}`).join(' ');
    return <G opacity={fade}>
      <Polyline points={points} fill="none" stroke={color} strokeWidth="8" strokeLinejoin="round" />
      <Polyline points={points} fill="none" stroke={c.text} strokeWidth="2" strokeLinejoin="round" />
    </G>;
  }
  const x = effect.x ?? 0;
  const y = effect.y ?? 0;
  const r = effect.r ?? 0;
  if (kind === 'whip') {
    return <G opacity={fade}>
      <Ellipse cx={x} cy={y} rx={r * .78} ry={r * .22} fill={color} fillOpacity={fade * .16}
        stroke={color} strokeWidth="4" />
      <Line x1={x - r * .8} y1={y} x2={x + r * .8} y2={y} stroke={c.text} strokeWidth="2" opacity=".8" />
    </G>;
  }
  if (kind === 'bolt') {
    const points = [
      [x, y - r], [x - 9, y - r * .55], [x + 8, y - r * .3], [x - 7, y],
      [x + 9, y + r * .35], [x - 5, y + r * .62], [x, y + r],
    ].map(([px, py]) => `${px},${py}`).join(' ');
    return <G opacity={fade}>
      <Polyline points={points} fill="none" stroke={color} strokeWidth="5" strokeLinejoin="round" />
      <Polyline points={points} fill="none" stroke={c.text} strokeWidth="1.5" strokeLinejoin="round" />
    </G>;
  }
  if (kind === 'nova') {
    return <G opacity={fade}>
      <Circle cx={x} cy={y} r={r * (1 - .55 * fade)} fill="none" stroke={color} strokeWidth="8" />
      <Circle cx={x} cy={y} r={r * (1 - .55 * fade) * .82} fill="none" stroke={c.text} strokeWidth="2" opacity=".65" />
    </G>;
  }
  return <Circle cx={x} cy={y} r={Math.max(4, r * (1 - .55 * fade))}
    fill={color} fillOpacity={fade * .18} stroke={color} strokeOpacity={fade} strokeWidth="4" />;
}

const clamp = (x: number, low: number, high: number) => Math.min(high, Math.max(low, x));
const mix = (a: number, b: number, t: number) => a + (b - a) * t;

interface Props {
  game: AbyssState;
  uid?: number;
  size: { width: number; height: number };
  input: React.RefObject<XY>;
  connected: boolean;
}

export default function AbyssBoard({ game, uid, size, input, connected }: Props) {
  const latest = useRef({ game, at: 0, previous: game });
  const local = useRef<XY | null>(null);
  const [frame, setFrame] = useState<{
    previous: AbyssState;
    fraction: number;
    position: XY | null;
    direction: XY;
  } | null>(null);
  useEffect(() => {
    const prior = latest.current;
    const gap = game.frame - prior.game.frame;
    latest.current = { game, at: performance.now(), previous: gap > 0 && gap <= 5 ? prior.game : game };
    if (gap < 0 || gap > 5) local.current = null;
  }, [game]);
  useEffect(() => {
    if (!connected || game.result) {
      local.current = null;
      return;
    }
    let id: number;
    let previous = 0;
    function animate(now: number) {
      if (!previous || now - previous >= 30) {
        const dt = clamp((now - previous) / 1000, 0, .05);
        previous = now;
        const { game: state, at, previous: lastState } = latest.current;
        const direction = input.current;
        const me = state.players.find(p => p.id === uid);
        if (me) {
          const p = local.current ?? me.position;
          const canMove = me.hp > 0 && !state.choice_seconds && now - at < 450;
          const [dx, dy] = canMove ? direction : [0, 0];
          const speed = me.move_speed || 158;
          const gap = Math.hypot(p[0] - me.position[0], p[1] - me.position[1]);
          const blend = gap > 82 || !canMove ? 1 : dx || dy ? .24 : .62;
          local.current = [
            clamp(mix(p[0], me.position[0], blend) + dx * speed * dt, 22, abyssWorld.width - 22),
            clamp(mix(p[1], me.position[1], blend) + dy * speed * dt, 22, abyssWorld.height - 22),
          ];
        }
        setFrame({ previous: lastState, fraction: clamp((now - at) / 92, 0, 1),
          position: local.current, direction });
      }
      id = requestAnimationFrame(animate);
    }
    id = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(id);
  }, [connected, game.result, input, uid]);

  if (!size.width || !size.height) return null;
  const zoom = Math.min(1, size.width / 520, size.height / 440);
  const width = size.width / zoom;
  const height = size.height / zoom;
  const me = game.players.find(p => p.id === uid);
  const observer = game.players.find(p => p.hp > 0);
  const ownPosition = connected && !game.result ? frame?.position ?? me?.position : me?.position;
  const [centerX, centerY] = (me?.out ? observer?.position : ownPosition) ?? [abyssWorld.width / 2, abyssWorld.height / 2];
  const left = width >= abyssWorld.width ? (abyssWorld.width - width) / 2 : clamp(centerX - width / 2, 0, abyssWorld.width - width);
  const top = height >= abyssWorld.height ? (abyssWorld.height - height) / 2 : clamp(centerY - height / 2, 0, abyssWorld.height - height);
  const previous = frame?.previous ?? game;
  const fraction = !connected || game.result ? 1 : (frame?.fraction ?? 1);
  const visible = (x: number, y: number) => x > left - 75 && x < left + width + 75 && y > top - 75 && y < top + height + 75;
  const enemyPositions = new Map(previous.enemies.map(e => [e[0], e]));
  const playerPositions = new Map(previous.players.map(p => [p.id, p.position]));

  return <View style={StyleSheet.absoluteFill} pointerEvents="none">
    <Image source={floorImage} resizeMode="stretch" style={{
      position: 'absolute', width: abyssWorld.width * zoom, height: abyssWorld.height * zoom,
      left: -left * zoom, top: -top * zoom,
    }} />
    <Svg style={StyleSheet.absoluteFill} width={size.width} height={size.height} viewBox={`${left} ${top} ${width} ${height}`}>
      <Defs>
        <RadialGradient id="abyss-visibility">
          <Stop offset="0" stopColor={c.signal} stopOpacity=".08" />
          <Stop offset=".45" stopColor={c.deep} stopOpacity="0" />
          <Stop offset="1" stopColor={c.deep} stopOpacity=".7" />
        </RadialGradient>
      </Defs>
      <Rect x={left} y={top} width={width} height={height} fill="url(#abyss-visibility)" />
      {game.auras.filter(([, , x, y]) => visible(x, y)).map(([owner, weapon, x, y, r]) =>
        <Circle key={`aura-${owner}-${weapon}`} cx={x} cy={y} r={r}
          fill={weaponColor(weapon)} fillOpacity=".07" stroke={weaponColor(weapon)}
          strokeOpacity=".35" strokeWidth="2" strokeDasharray="4 6" />)}
      {game.fields.filter(([, , x, y]) => visible(x, y)).map(([id, kind, x, y, r]) =>
        <G key={id}>
          <Circle cx={x} cy={y} r={r} fill={weaponColor(kind)} fillOpacity=".18"
            stroke={weaponColor(kind)} strokeDasharray={kind === 'spore' || kind === 'la_borra' ? undefined : '5 6'} />
          {(kind === 'spore' || kind === 'la_borra') && [-1, 0, 1].map(i =>
            <Circle key={i} cx={x + 24 * i} cy={y + 12 * (i * i - 1)} r={7}
              fill={weaponColor(kind)} fillOpacity=".5" />)}
        </G>)}
      {game.loot.filter(([, x, y]) => visible(x, y)).map(([id, x, y]) =>
        <G key={id}><Circle cx={x} cy={y} r="9" fill={c.beacon} opacity=".15" />
          <Polygon points={`${x},${y - 5} ${x + 4},${y} ${x},${y + 5} ${x - 4},${y}`} fill={c.beacon} stroke={c.text} />
        </G>)}
      {game.chests.filter(([, x, y]) => visible(x, y)).map(([id, x, y]) =>
        <Chest key={id} x={x} y={y} />)}
      {game.enemies.filter(([, , x, y]) => visible(x, y)).map(([id, kind, x, y, angle, hp, max]) => {
        const old = enemyPositions.get(id);
        const px = old ? mix(old[2], x, fraction) : x, py = old ? mix(old[3], y, fraction) : y;
        return <G key={id} transform={`translate(${px} ${py})`}>
          <G rotation={angle * radians}><Creature kind={kind} /></G>
          {(kind === 'warden' || kind === 'elite') && <>
            <Rect x="-33" y="-36" width="66" height="5" rx="2" fill={c.shadow} />
            <Rect x="-33" y="-36" width={66 * hp / max} height="5" rx="2" fill={c[kind]} />
          </>}
        </G>;
      })}
      {game.players.filter(p => !p.out).map(p => {
        const old = playerPositions.get(p.id) ?? p.position;
        const mine = p.id === uid;
        const [dx, dy] = frame?.direction ?? [0, 0];
        const angle = mine && connected && p.hp && !game.choice_seconds && !game.result && (dx || dy) ? Math.atan2(dy, dx) : p.angle;
        const [x, y] = mine && ownPosition ? ownPosition : [mix(old[0], p.position[0], fraction), mix(old[1], p.position[1], fraction)];
        return <G key={p.id} transform={`translate(${x} ${y})`}>
          {mine && <Circle r="25" fill="none" stroke={c.signal} strokeDasharray="3 5" />}
          {!p.hp && <Circle r="51" fill={c.charge} fillOpacity=".13" stroke={c.charge} strokeDasharray="4 5" />}
          <G rotation={angle * radians}><Diver mine={mine} down={!p.hp} /></G>
          <Text y="-24" fontSize="13" fontWeight="bold" textAnchor="middle" fill={c.text} stroke={c.deep} strokeWidth=".4">
            {p.hp ? (mine ? '你' : p.name.slice(0, 10)) : `待救援 ${p.down}s`}
          </Text>
        </G>;
      })}
      {game.orbits.filter(([, , x, y]) => visible(x, y)).map(([owner, weapon, x, y, r, index]) =>
        <G key={`orbit-${owner}-${weapon}-${index}`} transform={`translate(${x} ${y})`}>
          <Circle r={r + 4} fill={weaponColor(weapon)} fillOpacity=".16" />
          <Rect x="-5" y="-5" width="10" height="10" rx="2"
            fill={weaponColor(weapon)} stroke={c.shadow} strokeWidth="1.5" />
        </G>)}
      {game.shots.filter(([, , x, y]) => visible(x, y)).map(([id, weapon, x, y, angle, r]) =>
        <Shot key={id} weapon={weapon} x={x} y={y} angle={angle} r={r} />)}
      {game.effects.map(e => {
        const fade = clamp((e.until - game.frame - fraction * 2) / 7, 0, 1);
        return <Effect key={e.id} effect={e} fade={fade} />;
      })}
    </Svg>
  </View>;
}