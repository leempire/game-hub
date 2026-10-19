/**
 * 军棋棋盘渲染层（对局屏与战绩回放共用一份画法）。
 *
 * **地形全部来自服务端下发的 `topology`** —— 这个文件一行拓扑规则都不写：
 * 哪两个点之间有铁路、哪里是行营/大本营/山界，都是数据。铁路画粗双线、
 * 公路画细线（形状通道；两条线的颜色差只有 1.35:1，靠色相分不出来）。
 *
 * 坐标：引擎里 `row 0` 是上方（黑方）端线。黑方视角由 `flipped` 做上下翻转，
 * 保证「自己的半场永远在下方」。**testID 用屏幕坐标**（翻转后的行），
 * 所以 E2E 点 `lz-cell-6-0` 点到的就是屏幕上那一格。
 */

import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, G, Line, Path, Rect } from 'react-native-svg';

import { LzqView, pieceName } from '../luzhanqi';
import {
  LzqLayout, cellCenter, colLeft, flipRow, frontBand, pieceRect, rowTop,
} from '../luzhanqiBoard';
import {
  colors, fontSize, lastMoveMark, lastMoveMarkEdge, luzhanqiAccent, luzhanqiBack,
  luzhanqiBackStar, luzhanqiBlack, luzhanqiBoard, luzhanqiCamp, luzhanqiFace,
  luzhanqiFaceEdge, luzhanqiHq, luzhanqiRail, luzhanqiRed, luzhanqiRoad, radius,
} from '../theme';

export interface LuzhanqiBoardProps {
  view: LzqView;
  layout: LzqLayout;
  /** 黑方视角时为 true（自己的端线画在下方）。 */
  flipped: boolean;
  /** 选中的格子（屏幕坐标）。 */
  selected?: [number, number] | null;
  /** 可落点集合，键是 `"r,c"`（屏幕坐标）。 */
  targets?: Set<string>;
  /** 布阵阶段第二次点击的候选格（画成待交换态）。 */
  swapSel?: [number, number] | null;
  /** 刚发生碰子的格子（屏幕坐标），画一圈闪光。 */
  battleAt?: [number, number] | null;
  onCellPress?: (r: number, c: number) => void;
  /** 布阵阶段：可交换的格子。 */
  interactive?: boolean;
}

export default function LuzhanqiBoardView({
  view, layout, flipped, selected, targets, swapSel, battleAt,
  onCellPress, interactive = true,
}: LuzhanqiBoardProps) {
  const rows = view.rows;
  const cols = view.cols;
  const topo = view.topology;
  const toDisp = (r: number) => (flipped ? flipRow(r, rows) : r);
  const key = (r: number, c: number) => `${r},${c}`;

  const band = frontBand(layout, rows);
  const campR = Math.min(layout.cellW, layout.cellH) * 0.34;
  const railW = Math.max(3, Math.min(layout.cellW, layout.cellH) * 0.13);
  const roadW = Math.max(1, railW * 0.32);

  return (
    <View style={[styles.wrap, { width: layout.width, height: layout.height }]}>
      <Svg width={layout.width} height={layout.height} style={StyleSheet.absoluteFill}>
        <Rect x={0} y={0} width={layout.width} height={layout.height}
          fill={luzhanqiBoard} rx={8} />
        {/* 两半之间那条「前线 / 山界」带，底色压暗一档，一眼看出这是分界 */}
        <Rect x={0} y={band.top} width={layout.width} height={band.height}
          fill={luzhanqiRoad} opacity={0.18} />
        <G>
          {topo.roads.map(([r1, c1, r2, c2], i) => {
            const a = cellCenter(toDisp(r1), c1, layout);
            const b = cellCenter(toDisp(r2), c2, layout);
            return <Line key={`rd${i}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              stroke={luzhanqiRoad} strokeWidth={roadW} />;
          })}
        </G>
        {/* 铁路：粗线打底 + 中线回填 = 双线（形状通道，不靠颜色） */}
        <G>
          {topo.rails.map(([r1, c1, r2, c2], i) => {
            const a = cellCenter(toDisp(r1), c1, layout);
            const b = cellCenter(toDisp(r2), c2, layout);
            return (
              <G key={`rl${i}`}>
                <Line x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={luzhanqiRail} strokeWidth={railW} strokeLinecap="round" />
                <Line x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={luzhanqiBoard} strokeWidth={railW * 0.34} strokeLinecap="round" />
              </G>
            );
          })}
        </G>
        {/* 前线：三条过界铁路画虚线；山界：两处双层折线（形状通道） */}
        <G>
          {topo.fronts.map(([r1, c1, r2, c2], i) => {
            const a = cellCenter(toDisp(r1), c1, layout);
            const b = cellCenter(toDisp(r2), c2, layout);
            return <Line key={`fr${i}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              stroke={luzhanqiRail} strokeWidth={roadW} strokeDasharray="4,3" />;
          })}
          {topo.mountains.map(([r1, c1], i) => {
            const a = cellCenter(toDisp(r1), c1, layout);
            const w = layout.cellW * 0.30;
            const h = layout.cellH * 0.34;
            return (
              <G key={`mt${i}`}>
                <Path d={`M${a.x - w} ${a.y + h} L${a.x} ${a.y - h} L${a.x + w} ${a.y + h}`}
                  stroke={luzhanqiRail} strokeWidth={railW * 0.5} fill="none" />
                <Path d={`M${a.x - w * 0.6} ${a.y + h * 1.5} L${a.x} ${a.y - h * 0.2} L${a.x + w * 0.6} ${a.y + h * 1.5}`}
                  stroke={luzhanqiRail} strokeWidth={railW * 0.35} fill="none" opacity={0.75} />
              </G>
            );
          })}
        </G>
        {/* 行营（圆）与大本营（圆角方块）—— 靠形状区分 */}
        <G>
          {topo.kinds.map((row, r) => row.map((kind, c) => {
            if (kind === 'post') return null;
            const p = cellCenter(toDisp(r), c, layout);
            if (kind === 'camp') {
              return <Circle key={`cp${r}-${c}`} cx={p.x} cy={p.y} r={campR}
                fill={luzhanqiCamp} stroke={luzhanqiRail} strokeWidth={roadW * 1.1} />;
            }
            const w = layout.cellW * 0.74;
            const h = layout.cellH * 0.72;
            return <Rect key={`hq${r}-${c}`} x={p.x - w / 2} y={p.y - h / 2}
              width={w} height={h} rx={4}
              fill={luzhanqiHq} stroke={luzhanqiRail} strokeWidth={roadW * 1.3} />;
          }))}
        </G>
      </Svg>

      {/* 格子层：**每一格都是可点区域**（空格也要能点 —— 走子多数时候就是走到空格上）。
          棋子画在格子里面，只是个视觉层。 */}
      {Array.from({ length: rows }).flatMap((_, r) => Array.from({ length: cols }).map((__, c) => {
        const dr = toDisp(r);
        const cell = view.cells[r][c];
        const isSel = !!selected && selected[0] === dr && selected[1] === c;
        const isSwap = !!swapSel && swapSel[0] === dr && swapSel[1] === c;
        const isTarget = targets?.has(key(dr, c)) ?? false;
        const isLast = !!view.last_move
          && ((view.last_move.from[0] === r && view.last_move.from[1] === c)
            || (view.last_move.to[0] === r && view.last_move.to[1] === c));
        const sideColor = cell?.c === 'red' ? luzhanqiRed : luzhanqiBlack;
        const hidden = !!cell && cell.p === null;
        const rect = pieceRect(dr, c, layout);
        return (
          <Pressable
            key={`cell${r}-${c}`}
            testID={`lz-cell-${dr}-${c}`}
            disabled={!interactive}
            onPress={() => onCellPress?.(dr, c)}
            style={{
              position: 'absolute',
              left: colLeft(c, layout),
              top: rowTop(dr, layout),
              width: layout.cellW,
              height: layout.cellH,
            }}
          >
            {cell && (
              <View
                testID={hidden ? `lz-back-${dr}-${c}` : `lz-piece-${dr}-${c}`}
                style={[styles.piece, {
                  left: layout.pad,
                  top: layout.pad,
                  width: rect.width,
                  height: rect.height,
                  backgroundColor: hidden ? luzhanqiBack : luzhanqiFace,
                  borderColor: isSel || isSwap ? luzhanqiAccent
                    : (hidden ? luzhanqiBackStar : sideColor),
                  borderWidth: isSel || isSwap ? 2.5 : 1.6,
                }]}
              >
                {hidden ? (
                  <Star size={Math.min(rect.width, rect.height) * 0.62} />
                ) : (
                  <Text
                    numberOfLines={1}
                    style={[styles.pieceText, {
                      color: sideColor,
                      fontSize: Math.max(9, Math.min(rect.width / 2.4, rect.height * 0.46)),
                    }]}
                  >
                    {pieceName(cell.p)}
                  </Text>
                )}
                {isLast && (
                  <View
                    testID={`lz-last-${dr}-${c}`}
                    style={[styles.lastMark, { borderColor: lastMoveMarkEdge }]}
                  />
                )}
                {!!battleAt && battleAt[0] === dr && battleAt[1] === c && (
                  <View style={[styles.battleFlash, { borderColor: luzhanqiAccent }]} />
                )}
              </View>
            )}
            {/* 可落点：空位画实心圆点、可吃的敌子画环 —— 形状区分，不只看颜色 */}
            {isTarget && (
              <View
                testID={`lz-target-${dr}-${c}`}
                pointerEvents="none"
                style={[styles.target, cell ? styles.targetRing : styles.targetDot,
                  { borderColor: luzhanqiAccent, backgroundColor: cell ? 'transparent' : luzhanqiAccent }]}
              />
            )}
            {!cell && isLast && (
              <View
                testID={`lz-last-${dr}-${c}`}
                pointerEvents="none"
                style={[styles.lastMark, styles.lastMarkPlain, { borderColor: lastMoveMarkEdge }]}
              />
            )}
          </Pressable>
        );
      }))}
    </View>
  );
}

/** 暗棋的牌背五角星（形状通道，与正面的汉字区分开）。 */
function Star({ size }: { size: number }) {
  const r = size / 2;
  const pts: string[] = [];
  for (let i = 0; i < 10; i++) {
    const rad = (Math.PI / 5) * i - Math.PI / 2;
    const rr = i % 2 === 0 ? r : r * 0.45;
    pts.push(`${r + rr * Math.cos(rad)},${r + rr * Math.sin(rad)}`);
  }
  return (
    <Svg width={size} height={size}>
      <Path d={`M${pts.join(' L')} Z`} fill={luzhanqiBackStar} />
    </Svg>
  );
}

const styles = StyleSheet.create({
  wrap: { position: 'relative' },
  piece: {
    position: 'absolute',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radius.sm,
  },
  pieceText: { fontWeight: '700' },
  target: {
    position: 'absolute',
    left: '50%',
    top: '50%',
  },
  /** 空位：实心圆点。 */
  targetDot: {
    width: 12,
    height: 12,
    marginLeft: -6,
    marginTop: -6,
    borderRadius: 6,
    opacity: 0.85,
  },
  /** 可吃的敌子：套在棋子外面的一圈环（形状通道，与空位点区分开）。 */
  targetRing: {
    left: 2,
    top: 2,
    right: 2,
    bottom: 2,
    borderWidth: 3,
    borderRadius: radius.sm,
    opacity: 0.9,
  },
  lastMark: {
    position: 'absolute',
    right: 1,
    top: 1,
    width: 7,
    height: 7,
    borderRadius: 3.5,
    borderWidth: 1,
    backgroundColor: lastMoveMark,
  },
  lastMarkPlain: {
    right: 3,
    top: 3,
    width: 9,
    height: 9,
    borderRadius: 4.5,
  },
  battleFlash: {
    position: 'absolute',
    left: -3,
    top: -3,
    right: -3,
    bottom: -3,
    borderRadius: radius.sm + 3,
    borderWidth: 2.5,
  },
});
