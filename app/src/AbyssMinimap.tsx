import React, { memo } from 'react';
import { StyleSheet, View } from 'react-native';
import Svg, { Circle, G, Line, Path, Rect, Text } from 'react-native-svg';
import { abyssWorld, type AbyssCacheKind, type AbyssState } from './abyss';
import { abyssColors as c } from './theme';

const cacheStyles = {
  xp: { label: '经验', color: c.beacon, shape: 'M0-6L5 0L0 6L-5 0Z' },
  repair: { label: '修补', color: c.charge, shape: 'M-2-6H2V-2H6V2H2V6H-2V2H-6V-2H-2Z' },
  gear: { label: '装备', color: c.signal, shape: 'M-5-3L0-6L5-3V3L0 6L-5 3Z' },
  salvage: { label: '打捞', color: c.chest, shape: 'M-5-4H5V4H-5Z' },
} satisfies Record<AbyssCacheKind, { label: string; color: string; shape: string }>;

export function AbyssCacheMarker({ kind, x, y, id }: {
  kind: AbyssCacheKind; x: number; y: number; id: number;
}) {
  const { label, color, shape } = cacheStyles[kind];
  return <G testID={`abyss-cache-${id}`} transform={`translate(${x} ${y})`}>
    <Circle r={22} fill={c.deep} fillOpacity=".85" stroke={color} strokeWidth={2} />
    <Path d={shape} fill={color} scale={2.2} />
    <Text y={39} textAnchor="middle" fill={c.text} stroke={c.deep} strokeWidth={.65}
      fontWeight="bold" fontSize={15}>{label}</Text>
  </G>;
}

const mapWidth = 144;
const mapHeight = 100;
const padding = 9;
const legend = Object.entries(cacheStyles);

export function abyssMinimapLayout(width: number, arenaHeight: number, noticeHeight: number) {
  const bottom = noticeHeight ? noticeHeight + 20 : 7;
  const heightRatio = (mapHeight + 16) / mapWidth;
  const mapSize = Math.min(144, Math.max(106, width * .34), (arenaHeight - bottom - 16) / heightRatio);
  return mapSize > 0 ? { width: mapSize, height: mapSize * heightRatio, bottom } : null;
}

export default memo(function AbyssMinimap({ game, uid, watching, offlinePeers, bottom, width, height }: {
  game: AbyssState;
  uid?: number;
  watching?: number;
  offlinePeers: ReadonlySet<number>;
  bottom: number;
  width: number;
  height: number;
}) {
  const players = game.players.filter(player => !player.out);
  const tracked = players.find(player => player.id === (watching ?? uid)) ?? players[0];
  const [mapX, mapY] = game.bounds ?? [abyssWorld.width, abyssWorld.height];
  const caches = game.caches ?? [];
  const threats = game.enemies.filter(([, kind, , , , hp]) => hp > 0 && (kind === 'elite' || kind === 'warden'));
  const [cx, cy] = tracked?.position ?? [0, 0];
  const bounded = mapX !== null && mapY !== null;
  let xSpan = mapX === null ? 720 : mapX / 2;
  let ySpan = mapY === null ? 500 : mapY / 2;
  if (!bounded) {
    const points = [
      ...players.map(player => player.position),
      ...caches.map(([, , x, y]) => [x, y]),
      ...game.chests.map(([, x, y]) => [x, y]),
      ...threats.map(([, , x, y]) => [x, y]),
    ];
    for (const [x, y] of points) {
      xSpan = Math.max(xSpan, Math.ceil(Math.abs(x - cx) / 250) * 250);
      ySpan = Math.max(ySpan, Math.ceil(Math.abs(y - cy) / 250) * 250);
    }
  }
  const scale = Math.min((mapWidth / 2 - padding) / xSpan, (mapHeight / 2 - padding) / ySpan);
  const mapAt = (x: number, y: number) => `translate(${mapWidth / 2 + (x - (bounded ? mapX / 2 : cx)) * scale}
    ${mapHeight / 2 + (y - (bounded ? mapY / 2 : cy)) * scale})`;
  const description = legend.map(([kind, { label }]) =>
    `${label}补给 ${caches.filter(([, cacheKind]) => cacheKind === kind).length} 处`).join('，');
  return <View testID="abyss-minimap" pointerEvents="none" style={[styles.panel, { bottom }]}
    accessible accessibilityRole="image" accessibilityLabel={`小地图。实心三角为当前视角，圆点为队友，尖箭为精英或守卫。${description}`}>
    <Svg width={width} height={height} viewBox={`0 0 ${mapWidth} ${mapHeight + 16}`}>
      {bounded && <Rect x={mapWidth / 2 - xSpan * scale} y={mapHeight / 2 - ySpan * scale}
        width={xSpan * scale * 2} height={ySpan * scale * 2} fill="none" stroke={c.edge} />}
      <Line x1={padding} x2={mapWidth - padding} y1={50} y2={50} stroke={c.edge} strokeOpacity=".55" />
      <Line x1={72} x2={72} y1={padding} y2={mapHeight - padding} stroke={c.edge} strokeOpacity=".55" />
      {caches.map(([id, kind, x, y]) => <G key={id} testID={`abyss-map-cache-${id}`} transform={mapAt(x, y)}>
        <Path d={cacheStyles[kind].shape} scale={.68} fill={cacheStyles[kind].color} stroke={c.deep} strokeWidth={1} />
      </G>)}
      {game.chests.map(([id, x, y]) => <G key={id} testID={`abyss-map-chest-${id}`} transform={mapAt(x, y)}>
        <Rect x={-4} y={-3} width={8} height={6} rx={1} fill={c.deep} stroke={c.chest} strokeWidth={1.4} />
      </G>)}
      {threats.map(([id, kind, x, y]) => <G key={id} testID={`abyss-map-threat-${id}`} transform={mapAt(x, y)}>
        <Path d="M0-6L4 3L0 1L-4 3Z" fill={kind === 'warden' ? c.warden : c.elite} stroke={c.deep} />
      </G>)}
      {players.map(player => <G key={player.id} testID={`abyss-map-player-${player.id}`}
        transform={mapAt(...player.position)}>
        {player.id === tracked?.id ? <Path d="M0-6L4 4L0 2L-4 4Z" fill={c.text} stroke={c.deep}
          rotation={player.angle * 180 / Math.PI + 90} />
          : <Circle r={player.hp > 0 ? 3.6 : 4.4} fill={offlinePeers.has(player.id) ? c.deep : player.hp > 0 ? c.signal : c.charge}
            stroke={offlinePeers.has(player.id) ? c.muted : c.deep} strokeWidth={1.2} />}
      </G>)}
      {legend.map(([kind, { label, color, shape }], index) => <G key={kind} transform={`translate(${8 + 35 * index} 106)`}>
        <Path d={shape} fill={color} scale={.55} />
        <Text x={6} y={4} fill={c.text} fontSize={10}>{label}</Text>
      </G>)}
    </Svg>
  </View>;
});

const styles = StyleSheet.create({
  panel: { position: 'absolute', right: 7, borderWidth: 1, borderColor: c.edge,
    borderRadius: 7, backgroundColor: c.deep + 'EB' },
});
