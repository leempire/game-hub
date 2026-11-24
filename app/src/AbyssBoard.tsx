import React, { memo, useEffect, useId, useMemo, useRef, useState } from 'react';
import { AccessibilityInfo, Image, StyleSheet, View } from 'react-native';
import Svg, {
  Circle, Defs, Ellipse, G, Image as SvgImage, Line, Path, Polygon, Polyline,
  RadialGradient, Rect, Stop, Text, Use,
} from 'react-native-svg';
import { abyssLowPressure, abyssRescue, abyssWorld, type AbyssState, type AbyssWardenAttack, type AbyssWeapon, type XY } from './abyss';
import { abyssColors as c } from './theme';
import AbyssObjective from './AbyssObjective';
import { CreatureDefinitions, wardenImage } from './AbyssCreatures';

const floorImage = require('../assets/abyss/recovery-floor.webp');
const radians = 180 / Math.PI;
const rescueCircumference = 2 * Math.PI * abyssRescue.radius;

const weaponColor = (weapon: string) =>
  (c as unknown as Record<string, string>)[weapon] ?? c.signal;

const Diver = memo(function Diver({ mine, down }: { mine: boolean; down: boolean }) {
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
});

const Floor = memo(function Floor({ left, top, zoom, width, height, worldWidth, worldHeight }: {
  left: number; top: number; zoom: number; width: number; height: number;
  worldWidth: number | null; worldHeight: number | null;
}) {
  if (worldWidth !== null && worldHeight !== null) {
    return <Image source={floorImage} resizeMode="stretch" style={{
      position: 'absolute', left: 0, top: 0, width: worldWidth * zoom, height: worldHeight * zoom,
      transform: [{ translateX: -left * zoom }, { translateY: -top * zoom }],
    }} />;
  }

  const tiles = [];
  const { width: tileWidth, height: tileHeight } = abyssWorld;
  for (let row = Math.floor(top / tileHeight); row < Math.ceil((top + height) / tileHeight); row++) {
    for (let col = Math.floor(left / tileWidth); col < Math.ceil((left + width) / tileWidth); col++) {
      tiles.push(<Image key={`${row}:${col}`} source={floorImage} resizeMode="stretch" style={{
        position: 'absolute', left: (col * tileWidth - left) * zoom, top: (row * tileHeight - top) * zoom,
        width: tileWidth * zoom, height: tileHeight * zoom,
      }} />);
    }
  }
  return <>{tiles}</>;
});

const VisibilityOverlay = memo(function VisibilityOverlay({ width, height }: { width: number; height: number }) {
  const id = `abyss-visibility-${useId().replace(/[^a-z0-9_-]/gi, '')}`;
  return <>
    <Defs>
      <RadialGradient id={id}>
        <Stop offset="0" stopColor={c.signal} stopOpacity=".08" />
        <Stop offset=".45" stopColor={c.deep} stopOpacity="0" />
        <Stop offset="1" stopColor={c.deep} stopOpacity=".7" />
      </RadialGradient>
    </Defs>
    <Rect width={width} height={height} fill={`url(#${id})`} />
  </>;
});

const Wall = memo(function Wall({ width, height }: { width: number; height: number }) {
  return <Rect testID="abyss-wall" x={abyssWorld.wallInset} y={abyssWorld.wallInset}
    width={width - 2 * abyssWorld.wallInset} height={height - 2 * abyssWorld.wallInset}
    fill="none" stroke={c.brass} strokeWidth={2} strokeOpacity=".6" strokeDasharray="12 9" />;
});

const shotWeapons = new Set<AbyssWeapon>([
  'rivet', 'holy_wand', 'harpoon', 'thousand_edge', 'axe', 'death_spiral', 'cross', 'heaven_sword',
  'charge', 'hellfire', 'rune', 'no_future',
]);
const radialShotWeapons = new Set<AbyssWeapon>(['charge', 'hellfire', 'rune', 'no_future']);

function ShotArt({ weapon }: { weapon: AbyssWeapon }) {
  const color = weaponColor(weapon);
  if (weapon === 'harpoon' || weapon === 'thousand_edge') return <>
    <Path d="M-28 0L-11-3L-12 0L-11 3Z" fill={color} opacity=".42" />
    <Path d={weapon === 'thousand_edge'
      ? 'M-15-3L4-3L9-9L22 0L9 9L4 3L-15 3L-11 0Z'
      : 'M-13-2H4L8-6L20 0L8 6L4 2H-13L-10 0Z'}
      fill={color} stroke={c.shadow} strokeWidth="1.5" strokeLinejoin="round" />
    <Path d="M-8 0H14" fill="none" stroke={c.text} strokeWidth="1.5" strokeLinecap="round" />
  </>;
  if (weapon === 'axe' || weapon === 'death_spiral') return <>
    <Path d="M-17 2Q-13-12 2-16Q17-17 21-2L14-6Q14 4 1 13Q-8 19-18 13L-9 10Z"
      fill={color} stroke={c.shadow} strokeWidth="2" strokeLinejoin="round" />
    <Path d="M-13 2Q-7-13 8-13Q15-13 18-6M-11 13Q3 13 10 3"
      fill="none" stroke={c.text} strokeWidth="1.7" strokeLinecap="round" />
    <Circle r="3.5" fill={c.deep} stroke={color} strokeWidth="1.4" />
  </>;
  if (weapon === 'heaven_sword') return <>
    <Path d="M-22 0L-14-3H-7L-10-9L-4-10L0-5L12-4L26 0L12 4L0 5L-4 10L-10 9L-7 3H-14Z"
      fill={color} stroke={c.shadow} strokeWidth="2" strokeLinejoin="round" />
    <Path d="M-7 0H20" fill="none" stroke={c.text} strokeWidth="2" strokeLinecap="round" />
  </>;
  if (weapon === 'cross') return <>
    <Path d="M0-16L5-6L15-8L8 0L15 8L5 6L0 16L-5 6L-15 8L-8 0L-15-8L-5-6Z"
      fill={color} stroke={c.shadow} strokeWidth="2" strokeLinejoin="round" />
    <Path d="M-5 0L0-5L5 0L0 5Z" fill={c.deep} stroke={c.text} strokeWidth="1.3" />
  </>;
  const r = 12;
  if (weapon === 'charge' || weapon === 'hellfire') return <>
    <Path d={weapon === 'hellfire'
      ? 'M-29-10L-13-6L-19-15Q-5-7 3-12Q14-10 14 0Q14 10 3 12Q-5 7-19 15L-13 6L-29 10L-21 0Z'
      : 'M-22-7L-9-4L-13-11Q-4-6 3-10Q12-8 12 0Q12 8 3 10Q-4 6-13 11L-9 4L-22 7L-15 0Z'}
      fill={color} stroke={c.shadow} strokeWidth="1.6" strokeLinejoin="round" />
    <Path d="M-10 0Q-2-2 3-6Q8-4 9 0Q8 4 3 6Q-2 2-10 0Z" fill={c.text} />
  </>;
  if (weapon === 'rune' || weapon === 'no_future') return <>
    <Circle r={r} fill={c.deep} stroke={c.shadow} strokeWidth="3.5" />
    <Circle r={r * .88} fill="none" stroke={color} strokeWidth="2"
      strokeDasharray={weapon === 'no_future' ? `${r * .5} ${r * .19}` : undefined} />
    <Path d={`M${-r * .55} 0L0 ${-r * .47}L${r * .47} 0L0 ${r * .47}Z`}
      fill={color} opacity=".7" />
    <Line x1={r * .25} x2={r * .88} y1={0} y2={0} stroke={c.text} strokeWidth="2" strokeLinecap="round" />
  </>;
  return <>
    <Path d="M-22 0L-8-3L-9 3Z" fill={color} opacity=".4" />
    <Path d={weapon === 'holy_wand' ? 'M-12 0L-5-6H7L17 0L7 6H-5Z' : 'M-10-3H5L12 0L5 3H-10L-7 0Z'}
      fill={color} stroke={c.shadow} strokeWidth="1.5" strokeLinejoin="round" />
    <Path d="M-5 0H7" fill="none" stroke={c.text} strokeWidth="1.5" strokeLinecap="round" />
  </>;
}

const ShotDefinitions = memo(function ShotDefinitions({ prefix }: { prefix: string }) {
  return <Defs>
    {Array.from(shotWeapons, weapon =>
      <G key={weapon} id={`${prefix}-shot-${weapon}`}><ShotArt weapon={weapon} /></G>)}
  </Defs>;
});

function Shot({ prefix, id, weapon, x, y, angle, r }: {
  prefix: string; id: number; weapon: AbyssWeapon; x: number; y: number; angle: number; r: number;
}) {
  const scale = radialShotWeapons.has(weapon) ? ` scale(${r / 12})` : '';
  const transform = `translate(${x} ${y}) rotate(${angle * radians})${scale}`;
  const testID = weapon === 'rune' || weapon === 'no_future' ? `abyss-rune-${id}`
    : weapon === 'harpoon' || weapon === 'thousand_edge' ? `abyss-shot-${id}` : undefined;
  return <Use testID={testID} href={`#${prefix}-shot-${shotWeapons.has(weapon) ? weapon : 'rivet'}`} transform={transform} />;
}

const PickupDefinitions = memo(function PickupDefinitions({ prefix }: { prefix: string }) {
  return <Defs>
    <G id={`${prefix}-beacon`}>
      <Circle r="9" fill={c.beacon} opacity=".15" />
      <Polygon points="0,-5 4,0 0,5 -4,0" fill={c.beacon} stroke={c.text} />
    </G>
    <G id={`${prefix}-chest`}>
      <Rect x="-10" y="-7" width="20" height="15" rx="2" fill={c.chest} stroke={c.shadow} strokeWidth="1.5" />
      <Rect x="-10" y="-7" width="20" height="5" rx="2" fill={c.brass} />
      <Rect x="-2" y="-1" width="4" height="7" rx="1" fill={c.deep} />
    </G>
  </Defs>;
});

const Beacon = memo(function Beacon({ href, x, y }: { href: string; x: number; y: number }) {
  return <Use testID="abyss-loot" href={href} transform={`translate(${x} ${y})`} />;
});

type CombatEffect = AbyssState['effects'][number];

function describeEffect(effect: CombatEffect) {
  const x = effect.x ?? 0;
  const y = effect.y ?? 0;
  const radius = Math.max(10, effect.r ?? 0) + 5;
  if (!effect.points?.length) {
    return { effect, minX: x - radius, maxX: x + radius, minY: y - radius, maxY: y + radius };
  }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const points = effect.points.map(([px, py]) => {
    minX = Math.min(minX, px);
    maxX = Math.max(maxX, px);
    minY = Math.min(minY, py);
    maxY = Math.max(maxY, py);
    return `${px},${py}`;
  }).join(' ');
  return { effect, points, minX: minX - 5, maxX: maxX + 5, minY: minY - 5, maxY: maxY + 5 };
}

function Effect({ effect, points, fade, reduceMotion }: {
  effect: CombatEffect; points?: string; fade: number; reduceMotion: boolean;
}) {
  const color = weaponColor(effect.weapon);
  const kind = effect.kind ?? 'ring';
  if (points) {
    return <G testID={`abyss-effect-${effect.id}`} opacity={fade}>
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
  if (kind === 'impact') {
    const scale = r / 12 * (reduceMotion ? 1 : 1 - .35 * fade);
    return <Path testID={`abyss-projectile-impact-${effect.id}`}
      d="M-12 0H-5M5 0H12M0-12V-5M0 5V12M-6-6L-3-3M3 3L6 6"
      transform={`translate(${x} ${y}) scale(${scale})`}
      fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" opacity={fade} />;
  }
  if (kind === 'rescue') {
    const radius = reduceMotion ? r : mix(23, r, 1 - fade * fade);
    return <G testID="abyss-rescue-pulse" opacity={fade}>
      <Circle cx={x} cy={y} r={radius} fill="none" stroke={c.signal} strokeWidth="3" />
      <Circle cx={x} cy={y} r={radius - 6} fill="none" stroke={c.text} strokeWidth="1.5" />
    </G>;
  }
  if (kind === 'chest') {
    const scale = reduceMotion ? 1 : .7 + .3 * (1 - fade);
    return <G testID="abyss-chest-burst" opacity={fade} transform={`translate(${x} ${y}) scale(${scale})`}>
      <Circle r={r - 4} fill="none" stroke={c.chest} strokeWidth="2" strokeDasharray="3 5" />
      <Path d="M0-24L9-8L0 2L-9-8ZM-16-17L-19-23M16-17L19-23M-19 7H-26M19 7H26"
        fill={c.chest} stroke={c.text} strokeWidth="1.8" strokeLinecap="round" />
    </G>;
  }
  const radius = r * (reduceMotion ? 1 : 1 - .55 * fade);
  if (kind === 'nova') {
    return <G testID={`abyss-effect-${effect.id}`} opacity={fade}>
      <Circle cx={x} cy={y} r={radius} fill="none" stroke={color} strokeWidth="8" />
      <Circle cx={x} cy={y} r={radius * .82} fill="none" stroke={c.text} strokeWidth="2" opacity=".65" />
    </G>;
  }
  return <Circle testID={`abyss-effect-${effect.id}`} cx={x} cy={y} r={Math.max(4, radius)}
    fill={color} fillOpacity={fade * .18} stroke={color} strokeOpacity={fade} strokeWidth="4" />;
}

const clamp = (x: number, low: number, high: number) => Math.min(high, Math.max(low, x));
const mix = (a: number, b: number, t: number) => a + (b - a) * t;

const WardenAttack = memo(function WardenAttack({ attack, zones, sampleAge, reduceMotion, outline = false }: {
  attack: AbyssWardenAttack;
  zones: AbyssWardenAttack['zones'];
  sampleAge: number;
  reduceMotion: boolean;
  outline?: boolean;
}) {
  const { impact, kind, duration_ms: duration } = attack;
  // Only animate the current phase. The server alone changes warning into impact.
  const progress = duration ? Math.min(1, attack.progress + sampleAge / duration) : attack.progress;
  return <G testID={outline ? 'abyss-warden-outline' : 'abyss-warden-ground'} opacity={impact ? 1 - progress : 1}>
    {zones.map(([x, y, r], index) => <G key={index} transform={`translate(${x} ${y})`}>
      {outline ? <>
        <Circle r={r} fill="none" stroke={c.shadow} strokeWidth={8} />
        <Circle r={r} fill="none" stroke={impact ? c.text : c.charge} strokeWidth={impact ? 6 : 4}
          strokeDasharray={impact ? undefined : kind === 'strike' ? '9 6' : '18 5'} />
      </> : <>
        <Circle r={r} fill={c.charge} fillOpacity={impact ? .34 : .12} />
        {impact ? <Circle testID="abyss-warden-impact" r={r * (reduceMotion ? .75 : .45 + .45 * progress)}
          fill="none" stroke={c.text} strokeWidth={3} strokeOpacity=".8" /> : <>
          <Circle testID="abyss-warden-charge" r={reduceMotion ? r * .8 : Math.max(3, r * progress)}
            fill={c.charge} fillOpacity={reduceMotion ? .06 + .1 * progress : .09}
            stroke={c.charge} strokeWidth={2} strokeOpacity=".72" />
          {kind === 'strike' && <Path d="M-18 0H18M0-18V18" stroke={c.text} strokeWidth="2" />}
        </>}
      </>}
    </G>)}
  </G>;
});

const frameMs = 50;
const interpolationDelay = 125;
const maxJitterBuffer = 95;
const snapshotFreshnessMs = 400;
const predictionWindowMs = 180;
const hitIndicatorMs = 320;
const pickupIndicatorMs = 240;
const headingGraceMs = 180;
const noDirection: XY = [0, 0];

const orbitKey = (owner: number, weapon: AbyssWeapon, index: number) => `orbit-${owner}-${weapon}-${index}`;

function indexSnapshot(game: AbyssState) {
  const players = new Map(game.players.map(player => [player.id, player]));
  const orbits = new Map(game.orbits.map(([owner, weapon, x, y, hitRadius, index]) => {
    const position = players.get(owner)?.position ?? [x, y];
    const dx = x - position[0];
    const dy = y - position[1];
    return [orbitKey(owner, weapon, index), { position, angle: Math.atan2(dy, dx), radius: Math.hypot(dx, dy), hitRadius }] as const;
  }));
  return {
    game,
    enemies: new Map(game.enemies.map(enemy => [enemy[0], enemy])),
    players,
    shots: new Map(game.shots.map(shot => [shot[0], shot])),
    loot: new Map(game.loot.map(item => [item[0], item])),
    fields: new Map(game.fields.map(field => [field[0], field])),
    orbits,
  };
}

type Snapshot = ReturnType<typeof indexSnapshot>;

function mixAngle(a: number, b: number, t: number) {
  // Snapshot headings come from atan2, so at most one revolution needs wrapping.
  let delta = b - a;
  if (delta > Math.PI) delta -= 2 * Math.PI;
  else if (delta < -Math.PI) delta += 2 * Math.PI;
  return a + delta * t;
}

interface Props {
  game: AbyssState;
  uid?: number;
  watching?: number;
  offlinePeers: ReadonlySet<number>;
  bannerHeight: number;
  noticeHeight: number;
  size: { width: number; height: number };
  input: React.RefObject<XY>;
  connected: boolean;
}

const AbyssBoard = memo(function AbyssBoard({ game, uid, watching, offlinePeers, bannerHeight, noticeHeight, size, input, connected }: Props) {
  const [reduceMotion, setReduceMotion] = useState(false);
  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isReduceMotionEnabled().then(enabled => {
      if (mounted) setReduceMotion(enabled);
    }).catch(() => {});
    const listener = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduceMotion);
    return () => { mounted = false; listener.remove(); };
  }, []);

  const artPrefix = `abyss-art-${useId().replace(/[^a-z0-9_-]/gi, '')}`;
  const watchedId = useRef<number | undefined>(undefined);
  const timeline = useRef<{ snapshots: Snapshot[]; offset: number; jitter: number; receivedAt: number; hitAt: number; pickupAt: number }>({
    snapshots: [], offset: 0, jitter: 0, receivedAt: 0, hitAt: -Infinity, pickupAt: -Infinity,
  });
  const local = useRef<XY | null>(null);
  const [frame, setFrame] = useState<{
    from: Snapshot;
    to: Snapshot;
    fraction: number;
    position: XY | null;
    camera: XY | null;
    heading: number | undefined;
    hitOpacity: number;
    pickupOpacity: number;
    latestFrame: number;
    sampleAge: number;
  } | null>(null);
  const [worldWidth, worldHeight] = game.bounds ?? [abyssWorld.width, abyssWorld.height];
  const animating = connected && !game.result && !game.choice_seconds;
  useEffect(() => { watchedId.current = watching; }, [watching]);

  useEffect(() => {
    const buffer = timeline.current;
    const prior = buffer.snapshots.at(-1);
    const at = performance.now();
    const observedOffset = at - game.frame * frameMs;
    const gap = prior ? game.frame - prior.game.frame : 0;
    const reset = !animating || !prior || gap < 0 || gap > 7
      || (gap > 0 && at - buffer.receivedAt > snapshotFreshnessMs);
    if (reset) {
      buffer.snapshots = [];
      buffer.offset = observedOffset;
      buffer.jitter = 0;
      buffer.hitAt = -Infinity;
      buffer.pickupAt = -Infinity;
      local.current = null;
      setFrame(null);
      if (!animating) return;
    }
    const latest = indexSnapshot(game);
    if (!reset) {
      if (gap === 0) buffer.snapshots.pop();
      const oldMe = uid === undefined ? undefined : prior.players.get(uid);
      const newMe = uid === undefined ? undefined : latest.players.get(uid);
      if (gap > 0 && oldMe && newMe) {
        if (newMe.hp < oldMe.hp) {
          buffer.hitAt = at;
        } else if (newMe.hp > 0 && (newMe.xp_collected ?? 0) > (oldMe.xp_collected ?? 0)
          && at - buffer.hitAt >= hitIndicatorMs && at - buffer.pickupAt >= pickupIndicatorMs) {
          buffer.pickupAt = at;
        }
      }
      // Follow slow clock drift without moving the render clock on every network burst.
      if (gap > 0) {
        const drift = observedOffset - buffer.offset;
        buffer.jitter = clamp(Math.max(drift, buffer.jitter * .97), 0, maxJitterBuffer);
        buffer.offset += clamp(drift * .06, -2, 2);
      }
    }
    buffer.snapshots.push(latest);
    if (buffer.snapshots.length > 8) buffer.snapshots.shift();
    // Same-tick updates can change UI state without advancing movement.
    if (reset || gap > 0) buffer.receivedAt = at;
  }, [animating, game, uid]);

  useEffect(() => {
    if (!animating || !size.width || !size.height) return;
    let id: number;
    let previous: number | undefined;
    let renderAt: number | undefined;
    let previousBuffer: Snapshot[] | undefined;
    let correctedFrame: number | undefined;
    let correction: XY = noDirection;
    let correctionUntil = 0;
    let camera: XY | null = null;
    let heading: number | undefined;
    let headingUntil = 0;
    let published: typeof frame = null;
    function animate(now: number) {
      id = requestAnimationFrame(animate);
      const { snapshots, offset, jitter, receivedAt, hitAt, pickupAt } = timeline.current;
      const latest = snapshots.at(-1);
      if (!latest) return;

      const dt = previous === undefined ? 0 : clamp((now - previous) / 1000, 0, .05);
      previous = now;
      const target = (now - offset - interpolationDelay - jitter) / frameMs;
      if (renderAt === undefined || previousBuffer !== snapshots) {
        renderAt = Math.min(latest.game.frame, target);
        previousBuffer = snapshots;
        correctedFrame = undefined;
        heading = undefined;
        headingUntil = 0;
      } else {
        // Adjust playback speed as the buffer changes without rewinding the scene.
        const step = dt * 1000 / frameMs;
        renderAt = Math.min(latest.game.frame, renderAt + clamp(target - renderAt, step * .6, step * 1.15));
      }
      const cursor = renderAt;
      const next = snapshots.findIndex(snapshot => snapshot.game.frame >= cursor);
      const to = next < 0 ? latest : snapshots[next];
      const from = next > 0 ? snapshots[next - 1] : to;
      const span = to.game.frame - from.game.frame;
      const fraction = span ? clamp((cursor - from.game.frame) / span, 0, 1) : 1;

      const me = latest.players.get(uid ?? -1);
      const sampleAge = clamp(now - receivedAt, 0, snapshotFreshnessMs);
      const fresh = sampleAge < snapshotFreshnessMs;
      // Freeze forward prediction before a stall is long enough to discard the timeline.
      const predicting = sampleAge < predictionWindowMs + jitter;
      const direction = me?.hp && predicting ? input.current : noDirection;
      if (me?.hp) {
        if (direction[0] || direction[1]) {
          heading = Math.atan2(direction[1], direction[0]);
          // A release can arrive locally before the snapshot acknowledging the turn.
          headingUntil = now + headingGraceMs;
        } else if (now >= headingUntil || !fresh) {
          heading = heading === undefined || reduceMotion
            ? me.angle : mixAngle(heading, me.angle, 1 - Math.exp(-18 * dt));
        }
        let p = local.current ?? me.position;
        if (correctedFrame !== latest.game.frame) {
          const dx = me.position[0] - p[0];
          const dy = me.position[1] - p[1];
          const error = Math.hypot(dx, dy);
          // Reconcile once per snapshot, then spread the adjustment across render frames.
          // Leave room for the server tick and transit time while moving.
          const tolerance = direction[0] || direction[1] ? 12 : 0;
          const factor = error > tolerance ? Math.min((error - tolerance) / .18, 65) / error : 0;
          correction = error > 82 ? noDirection : [dx * factor, dy * factor];
          if (error > 82) p = me.position;
          correctionUntil = now + 180;
          correctedFrame = latest.game.frame;
        }
        // Stop predicting on stale data and settle on the last confirmed position.
        const distance = me.move_speed * dt;
        const correctionStep = predicting ? clamp((correctionUntil - now) / 1000, 0, dt) : 0;
        const settled = 1 - Math.exp(-10 * dt);
        let dx = correction[0] * correctionStep;
        let dy = correction[1] * correctionStep;
        // A correction should never reverse a held input, especially near the stick dead zone.
        const strength = Math.hypot(...direction);
        if (strength) {
          const along = (dx * direction[0] + dy * direction[1]) / strength;
          const restored = Math.max(0, -along - distance * strength * .5) / strength;
          dx += direction[0] * restored;
          dy += direction[1] * restored;
        }
        const x = fresh ? p[0] + dx + direction[0] * distance : mix(p[0], me.position[0], settled);
        const y = fresh ? p[1] + dy + direction[1] * distance : mix(p[1], me.position[1], settled);
        const { wallInset } = abyssWorld;
        local.current = [
          worldWidth === null ? x : clamp(x, wallInset, worldWidth - wallInset),
          worldHeight === null ? y : clamp(y, wallInset, worldHeight - wallInset),
        ];
      } else {
        local.current = me?.position ?? null;
        heading = undefined;
        headingUntil = 0;
      }
      if (me?.out) {
        const id = watchedId.current;
        const teammate = id === undefined ? undefined : to.players.get(id) ?? latest.players.get(id);
        if (teammate) {
          const [x, y] = teammate.position;
          const old = from.players.get(teammate.id)?.position ?? teammate.position;
          // When playback restarts, the paused view already shows the watched diver.
          const [cx, cy] = camera ?? teammate.position;
          const follow = reduceMotion ? 1 : 1 - Math.exp(-13 * dt);
          camera = [mix(cx, mix(old[0], x, fraction), follow), mix(cy, mix(old[1], y, fraction), follow)];
        }
      } else {
        camera = local.current;
      }
      const hitOpacity = clamp(1 - (now - hitAt) / hitIndicatorMs, 0, 1);
      const pickupOpacity = hitOpacity === 0 ? clamp(1 - (now - pickupAt) / pickupIndicatorMs, 0, 1) : 0;
      const position = local.current;
      const latestFrame = latest.game.frame;
      const timedOverlay = latest.game.warden_attack || latest.game.players.some(player =>
        player.protected && player.protection_ms !== undefined);
      const overlayAge = timedOverlay ? sampleAge : 0;
      if (published && published.from === from && published.to === to && published.fraction === fraction
        && published.latestFrame === latestFrame && published.sampleAge === overlayAge
        && published.position?.[0] === position?.[0] && published.position?.[1] === position?.[1]
        && published.camera?.[0] === camera?.[0] && published.camera?.[1] === camera?.[1]
        && published.heading === heading && published.hitOpacity === hitOpacity && published.pickupOpacity === pickupOpacity) return;

      published = {
        from, to, fraction, position, camera, heading, hitOpacity, pickupOpacity, latestFrame, sampleAge: overlayAge,
      };
      setFrame(published);
    }
    id = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(id);
  }, [animating, input, reduceMotion, size.height, size.width, uid, worldWidth, worldHeight]);

  const drawing = animating ? frame : null;
  const scene = drawing?.to.game ?? game;
  const effects = useMemo(() => scene.effects.map(describeEffect), [scene.effects]);
  const wardenAttack = game.result ? null : game.warden_attack;
  const snapshotAge = drawing?.latestFrame === game.frame ? drawing.sampleAge : 0;
  if (!size.width || !size.height) return null;
  const regularZoom = Math.min(1, size.width / 520, size.height / 440);
  const zoom = worldWidth === null || worldHeight === null ? regularZoom
    : Math.max(regularZoom, size.width / worldWidth, size.height / worldHeight);
  const width = size.width / zoom;
  const height = size.height / zoom;
  const previous = drawing?.from;
  const fraction = drawing?.fraction ?? 1;
  const sceneTime = previous ? mix(previous.game.frame, scene.frame, fraction) : scene.frame;
  const me = game.players.find(p => p.id === uid);
  const ownPosition = drawing?.position ?? me?.position;
  const spectatorPosition = watching === undefined ? undefined : game.players.find(player => player.id === watching)?.position;
  const [centerX, centerY] = drawing?.camera ?? spectatorPosition ?? ownPosition
    ?? [(worldWidth ?? abyssWorld.width) / 2, (worldHeight ?? abyssWorld.height) / 2];
  const left = worldWidth === null ? centerX - width / 2 : clamp(centerX - width / 2, 0, Math.max(0, worldWidth - width));
  const top = worldHeight === null ? centerY - height / 2 : clamp(centerY - height / 2, 0, Math.max(0, worldHeight - height));
  const visible = (x: number, y: number, padding = 75) => x > left - padding && x < left + width + padding
    && y > top - padding && y < top + height + padding;

  const warningZones = wardenAttack?.zones.filter(([x, y, radius]) => visible(x, y, radius + 4));

  return <View style={StyleSheet.absoluteFill} pointerEvents="none">
    <Floor left={left} top={top} zoom={zoom} width={width} height={height} worldWidth={worldWidth} worldHeight={worldHeight} />
    <Svg style={StyleSheet.absoluteFill} width={size.width} height={size.height} viewBox={`0 0 ${width} ${height}`}>
      <VisibilityOverlay width={width} height={height} />
      <CreatureDefinitions prefix={artPrefix} />
      <ShotDefinitions prefix={artPrefix} />
      <PickupDefinitions prefix={artPrefix} />
      <G testID="abyss-world" transform={`translate(${-left} ${-top})`}>
        {worldWidth !== null && worldHeight !== null && <Wall width={worldWidth} height={worldHeight} />}
        {wardenAttack && !!warningZones?.length && <WardenAttack attack={wardenAttack} zones={warningZones} sampleAge={snapshotAge} reduceMotion={reduceMotion} />}
        {scene.auras.map(([owner, weapon, x, y, r]) => {
          const old = previous?.players.get(owner)?.position;
          const px = owner === uid && ownPosition ? ownPosition[0] : old ? mix(old[0], x, fraction) : x;
          const py = owner === uid && ownPosition ? ownPosition[1] : old ? mix(old[1], y, fraction) : y;
          if (!visible(px, py, Math.max(r, 75))) return null;
          return <Circle key={`aura-${owner}-${weapon}`} cx={px} cy={py} r={r}
            fill={weaponColor(weapon)} fillOpacity=".07" stroke={weaponColor(weapon)}
            strokeOpacity=".35" strokeWidth="2" strokeDasharray="4 6" />;
        })}
        {scene.fields.map(([id, kind, x, y, r]) => {
          const old = previous?.fields.get(id);
          const px = old ? mix(old[2], x, fraction) : x;
          const py = old ? mix(old[3], y, fraction) : y;
          const radius = old ? mix(old[4], r, fraction) : r;
          if (!visible(px, py, Math.max(radius, 75))) return null;
          return <G key={id}>
            <Circle testID="abyss-field" cx={px} cy={py} r={radius} fill={weaponColor(kind)} fillOpacity=".18"
              stroke={weaponColor(kind)} strokeDasharray={kind === 'spore' || kind === 'la_borra' ? undefined : '5 6'} />
            {(kind === 'spore' || kind === 'la_borra') && [-1, 0, 1].map(i =>
              <Circle key={i} cx={px + 24 * i} cy={py + 12 * (i * i - 1)} r={7}
                fill={weaponColor(kind)} fillOpacity=".5" />)}
          </G>;
        })}
        {scene.loot.map(([id, x, y]) => {
          const old = previous?.loot.get(id);
          const px = old ? mix(old[1], x, fraction) : x;
          const py = old ? mix(old[2], y, fraction) : y;
          if (!visible(px, py)) return null;
          return <Beacon key={id} href={`#${artPrefix}-beacon`} x={px} y={py} />;
        })}
        {scene.chests.filter(([, x, y]) => visible(x, y)).map(([id, x, y]) =>
          <Use key={id} href={`#${artPrefix}-chest`} x={x} y={y} />)}
        {scene.enemies.map(([id, kind, x, y, angle, hp, max]) => {
          const old = previous?.enemies.get(id);
          const px = old ? mix(old[2], x, fraction) : x, py = old ? mix(old[3], y, fraction) : y;
          if (!visible(px, py)) return null;
          const degrees = (old ? mixAngle(old[4], angle, fraction) : angle) * radians;
          if (kind !== 'warden' && kind !== 'elite') {
            const hitScale = !reduceMotion && old && hp < old[5] ? 1 + .16 * Math.sin(Math.PI * fraction) : 1;
            const recoil = hitScale === 1 ? '' : ` scale(${hitScale} ${1 / hitScale})`;
            return <Use testID={`abyss-enemy-${id}`} key={id} href={`#${artPrefix}-creature-${kind}`}
              transform={`translate(${px} ${py}) rotate(${degrees})${recoil}`} />;
          }
          return <G key={id} transform={`translate(${px} ${py})`}>
            {kind === 'warden'
              ? <G rotation={degrees}><SvgImage href={wardenImage} x={-39} y={-39} width={78} height={78} /></G>
              : <Use href={`#${artPrefix}-creature-elite`} rotation={degrees} />}
            <Rect x="-33" y={kind === 'warden' ? -48 : -36} width="66" height="5" rx="2" fill={c.shadow} />
            <Rect x="-33" y={kind === 'warden' ? -48 : -36} width={66 * hp / max} height="5" rx="2" fill={c[kind]} />
          </G>;
        })}
        {scene.players.map(scenePlayer => {
          const mine = scenePlayer.id === uid;
          const p = mine && me ? me : scenePlayer;
          if (p.out) return null;
          const old = previous?.players.get(p.id);
          const priorPosition = old?.position ?? p.position;
          const angle = mine
            ? drawing?.heading ?? p.angle
            : old ? mixAngle(old.angle, p.angle, fraction) : p.angle;
          const [x, y] = mine && ownPosition ? ownPosition : [mix(priorPosition[0], p.position[0], fraction), mix(priorPosition[1], p.position[1], fraction)];
          if (!mine && !visible(x, y)) return null;
          const current = mine ? me : game.players.find(player => player.id === p.id);
          const protectedNow = p.hp > 0 && current?.protected && (current.protection_ms === undefined || current.protection_ms > snapshotAge);
          const lowPressure = mine && abyssLowPressure(p.hp, p.max_hp);
          const rescue = scenePlayer.rescue;
          const rescueProgress = rescue > 0 ? mix(old?.rescue ?? rescue, rescue, fraction) : 0;
          return <G testID={`abyss-diver-${p.id}`} key={p.id} transform={`translate(${x} ${y})`}>
            {protectedNow && <Circle testID={`abyss-protection-${p.id}`} r="29" fill="none"
              stroke={c.beacon} strokeWidth="2" strokeOpacity=".8" />}
            {mine && <Circle testID={lowPressure ? 'abyss-low-pressure-ring' : undefined} r="25" fill="none"
              stroke={lowPressure ? c.charge : c.signal} strokeWidth={lowPressure ? 2.5 : 1}
              strokeDasharray={lowPressure ? '12 5' : '3 5'} />}
            {mine && !!drawing?.hitOpacity && <Circle testID="abyss-local-hit" r={32}
              fill="none" stroke={c.charge} strokeWidth={4} opacity={drawing.hitOpacity} />}
            {mine && !!drawing?.pickupOpacity && <Path testID="abyss-local-pickup"
              d="M-35 5l5-5 5 5M25 5l5-5 5 5" fill="none" stroke={c.beacon}
              strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" opacity={drawing.pickupOpacity}
              transform={reduceMotion ? undefined : `translate(0 ${-8 * (1 - drawing.pickupOpacity)})`} />}
            {!p.hp && <>
              <Circle r={abyssRescue.radius} fill={c.charge} fillOpacity=".13" stroke={c.charge} strokeDasharray="4 5" />
              {rescueProgress > 0 && <Circle testID="abyss-rescue-progress" r={abyssRescue.radius} fill="none" stroke={c.signal} strokeWidth={4}
                rotation={-90} strokeLinecap="round" strokeDasharray={[rescueCircumference, rescueCircumference]}
                strokeDashoffset={rescueCircumference * (1 - clamp(rescueProgress / abyssRescue.ticks, 0, 1))} />}
            </>}
            <G rotation={angle * radians}><Diver mine={mine} down={!p.hp} /></G>
            <Text y="-24" fontSize="13" fontWeight="bold" textAnchor="middle" fill={c.text} stroke={c.deep} strokeWidth=".4">
              {p.hp ? (mine ? '你' : p.name.slice(0, 10)) : `待救援 ${p.down}s`}
            </Text>
          </G>;
        })}
        {scene.orbits.map(([owner, weapon, x, y, r, index]) => {
          const key = orbitKey(owner, weapon, index);
          const current = drawing?.to.orbits.get(key);
          const old = previous?.orbits.get(key);
          let px = x, py = y, hitRadius = r;
          if (current) {
            const prior = old ?? current;
            const centerX = owner === uid && ownPosition ? ownPosition[0] : mix(prior.position[0], current.position[0], fraction);
            const centerY = owner === uid && ownPosition ? ownPosition[1] : mix(prior.position[1], current.position[1], fraction);
            const angle = mixAngle(prior.angle, current.angle, fraction);
            const radius = mix(prior.radius, current.radius, fraction);
            px = centerX + radius * Math.cos(angle);
            py = centerY + radius * Math.sin(angle);
            hitRadius = mix(prior.hitRadius, current.hitRadius, fraction);
          }
          if (!visible(px, py)) return null;
          return <G testID="abyss-orbit" key={key} transform={`translate(${px} ${py})`}>
            <Circle r={hitRadius + 4} fill={weaponColor(weapon)} fillOpacity=".16" />
            <Rect x="-5" y="-5" width="10" height="10" rx="2"
              fill={weaponColor(weapon)} stroke={c.shadow} strokeWidth="1.5" />
          </G>;
        })}
        {scene.shots.map(([id, weapon, x, y, angle, r, launch]) => {
          const old = previous?.shots.get(id);
          let px = old ? mix(old[2], x, fraction) : x;
          let py = old ? mix(old[3], y, fraction) : y;
          if (!old && launch && scene.frame > launch[0]) {
            if (sceneTime < launch[0]) return null;
            const progress = (sceneTime - launch[0]) / (scene.frame - launch[0]);
            px = mix(launch[1], x, progress);
            py = mix(launch[2], y, progress);
          }
          if (!visible(px, py, Math.max(r * 2.6, 75))) return null;
          return <Shot key={id} prefix={artPrefix} id={id} weapon={weapon} r={r} x={px} y={py}
            angle={old ? mixAngle(old[4], angle, fraction) : angle} />;
        })}
        {effects.map(({ effect, points, minX, maxX, minY, maxY }) => {
          if (maxX < left || minX > left + width || maxY < top || minY > top + height) return null;
          const life = effect.kind === 'rescue' || effect.kind === 'chest' ? 14 : effect.kind === 'impact' ? 5 : 7;
          if (effect.kind === 'impact' && sceneTime < effect.until - life) return null;
          const fade = clamp((effect.until - sceneTime) / life, 0, 1);
          return fade > 0 ? <Effect key={effect.id} effect={effect} points={points} fade={fade} reduceMotion={reduceMotion} /> : null;
        })}
        {wardenAttack && !!warningZones?.length && <WardenAttack attack={wardenAttack} zones={warningZones} sampleAge={snapshotAge} reduceMotion={reduceMotion} outline />}
        {connected && !game.result && !game.choice_seconds && me && me.hp > 0 && ownPosition &&
          <AbyssObjective game={game} uid={me.id} position={ownPosition} offlinePeers={offlinePeers}
            left={left} top={top} width={width} height={height} zoom={zoom}
            bannerHeight={bannerHeight} noticeHeight={noticeHeight} />}
      </G>
    </Svg>
  </View>;
});

export default AbyssBoard;
