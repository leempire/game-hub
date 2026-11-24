import React, { memo } from 'react';
import { G, Path, Rect, Text } from 'react-native-svg';
import type { AbyssState, XY } from './abyss';
import { abyssColors as c } from './theme';

interface Props {
  game: AbyssState;
  uid: number;
  position: XY;
  offlinePeers: ReadonlySet<number>;
  left: number;
  top: number;
  width: number;
  height: number;
  zoom: number;
  bannerHeight: number;
  noticeHeight: number;
}

export default memo(function AbyssObjective({ game, uid, position, offlinePeers, left, top, width, height, zoom, bannerHeight, noticeHeight }: Props) {
  const viewWidth = width * zoom;
  const viewHeight = height * zoom;
  if (viewWidth < 44 || viewHeight < 50) return null;

  const outside = (x: number, y: number) => x < left || x > left + width || y < top || y > top + height;
  const [px, py] = position;
  let target: XY | undefined;
  let rescueSeconds: number | undefined;
  let closest = Infinity;
  for (const player of game.players) {
    if (player.id === uid || player.out || player.hp > 0 || player.down <= 0) continue;
    const [x, y] = player.position;
    if (!outside(x, y)) continue;
    const distance = (x - px) ** 2 + (y - py) ** 2;
    if (distance < closest) {
      target = player.position;
      rescueSeconds = player.down;
      closest = distance;
    }
  }
  if (!target) {
    for (const [, x, y] of game.chests) {
      if (!outside(x, y)) continue;
      const distance = (x - px) ** 2 + (y - py) ** 2;
      if (distance < closest) {
        target = [x, y];
        closest = distance;
      }
    }
  }
  let trackingWarden = false;
  if (!target && game.boss && !game.boss_dead) {
    const warden = game.enemies.find(enemy => enemy[1] === 'warden' && enemy[5] > 0);
    if (warden && outside(warden[2], warden[3])) {
      target = [warden[2], warden[3]];
      trackingWarden = true;
    }
  }
  let regroupName: string | undefined;
  if (!target && game.map_size === 'endless') {
    let nearest: AbyssState['players'][number] | undefined;
    for (const player of game.players) {
      if (player.id === uid || player.out || player.hp <= 0 || offlinePeers.has(player.id)) continue;
      const [x, y] = player.position;
      const distance = (x - px) ** 2 + (y - py) ** 2;
      if (distance < closest) {
        closest = distance;
        nearest = player;
      }
    }
    if (nearest && outside(...nearest.position)) {
      target = nearest.position;
      regroupName = nearest.name;
    }
  }
  if (!target) return null;

  const [x, y] = target;

  // Intersect the direction ray with a viewport inset large enough to keep the label visible.
  const defaultInset = Math.min(56, viewHeight / 2 - 18);
  const fullTop = Math.max(defaultInset, bannerHeight + 28);
  const fullBottom = viewHeight - Math.max(defaultInset, noticeHeight + 34);
  const compact = viewWidth < 96 || viewHeight < 100 || fullTop >= fullBottom;
  const topEdge = compact ? bannerHeight + 22 : fullTop;
  const bottomEdge = compact ? viewHeight - (noticeHeight ? noticeHeight + 28 : 16) : fullBottom;
  if (topEdge >= bottomEdge) return null;
  const centerY = (topEdge + bottomEdge) / 2;
  const dx = (x - left) * zoom - viewWidth / 2;
  const dy = (y - top) * zoom - centerY;
  const ray = Math.min(
    (viewWidth / 2 - (compact ? 16 : 42)) / Math.abs(dx),
    (bottomEdge - centerY) / Math.abs(dy),
  );
  const atX = left + width / 2 + dx * ray / zoom;
  const atY = top + (centerY + dy * ray) / zoom;
  // The camera may be clamped at a wall while the diver is off-center.
  const angle = Math.atan2(y - py, x - px) * 180 / Math.PI;
  const color = regroupName !== undefined ? c.signal : trackingWarden ? c.warden : rescueSeconds === undefined ? c.chest : c.charge;
  const label = regroupName !== undefined ? '队友' : trackingWarden ? '守卫' : rescueSeconds === undefined ? '宝箱' : `待救 ${rescueSeconds}s`;

  const regroupDistance = regroupName === undefined ? null : Math.round(Math.sqrt(closest));
  const distanceLabel = regroupDistance !== null && regroupDistance >= 10000
    ? `${(regroupDistance / 1000).toFixed(1)}k` : regroupDistance;
  const accessibilityLabel = regroupName !== undefined ? `队友 ${regroupName}，距离 ${regroupDistance} 像素` : label;
  if (compact) return <G testID="abyss-objective-compact" accessible accessibilityRole="image" accessibilityLabel={accessibilityLabel}
    transform={`translate(${atX} ${atY}) scale(${1 / zoom})`}>
    <Rect x={-11} y={-11} width={22} height={22} rx={7} fill={c.deep} stroke={color} strokeWidth={1.5} />
    <Path d="M7 0L-5 6L-2 0L-5-6Z" fill={color} rotation={angle} />
  </G>;

  return <G testID="abyss-objective" accessible accessibilityRole="image" accessibilityLabel={accessibilityLabel} transform={`translate(${atX} ${atY}) scale(${1 / zoom})`}>
    <Rect x={-36} y={-14} width={72} height={28} rx={8} fill={c.deep} stroke={color} strokeWidth={1.5} />
    <G testID="abyss-objective-arrow" transform={`translate(-23 0) rotate(${angle})`}>
      <Path d="M7 0L-5 6L-2 0L-5-6Z" fill={color} />
    </G>
    <Text x={7} y={regroupDistance === null ? 5 : -1} fill={c.text} fontSize={12} fontWeight="bold"
      textAnchor="middle">{label}</Text>
    {regroupDistance !== null && <Text testID="abyss-regroup-distance" x={7} y={10} fill={color} fontSize={10}
      textAnchor="middle">{distanceLabel} px</Text>}
  </G>;
});
