/**
 * 军棋棋盘的**绘制几何** —— 只把服务端下发的 `topology` 换算成像素。
 *
 * ⚠️ **一行拓扑规则都不写**：铁路/公路连接哪些点、哪里是行营、哪里是山界，
 * 全部来自 `game.topology`（服务端 `luzhanqi.py` 的 `TOPOLOGY` 是唯一来源）。
 * 这是深空休眠 v9 的教训：几何两份实现必然漂移，军棋地形有 133 条边，更不可能对齐。
 *
 * 坐标：`row 0` = 上方（黑方）端线，`row 11` = 下方（红方）端线。
 * 红方渲染在下方；黑方视角由屏幕层做上下翻转（同象棋）。
 */

import type { LzqTopology } from './luzhanqi';

export interface LzqLayout {
  cellW: number;
  cellH: number;
  /** 两半之间那条「前线 / 山界」带的厚度。 */
  gap: number;
  /** 棋子相对格子的内缩。 */
  pad: number;
  width: number;
  height: number;
}

export function makeLayout(topology: LzqTopology, cellW: number, cellH: number, gap: number): LzqLayout {
  return {
    cellW,
    cellH,
    gap,
    pad: Math.max(1.5, Math.min(cellW, cellH) * 0.06),
    width: topology.cols * cellW,
    height: topology.rows * cellH + gap,
  };
}

/** 行的上边缘 y：`row >= 6` 的那一半整体下移一个 gap（中间是前线带）。 */
export function rowTop(r: number, L: LzqLayout): number {
  return r * L.cellH + (r >= 6 ? L.gap : 0);
}

export function colLeft(c: number, L: LzqLayout): number {
  return c * L.cellW;
}

export function cellCenter(r: number, c: number, L: LzqLayout): { x: number; y: number } {
  return { x: colLeft(c, L) + L.cellW / 2, y: rowTop(r, L) + L.cellH / 2 };
}

/** 棋子（长方形小方块）在格内的矩形。 */
export function pieceRect(r: number, c: number, L: LzqLayout) {
  return {
    left: colLeft(c, L) + L.pad,
    top: rowTop(r, L) + L.pad,
    width: L.cellW - L.pad * 2,
    height: L.cellH - L.pad * 2,
  };
}

/**
 * 黑方视角的上下翻转（同象棋 `flipSquare`）：红方在下方，黑方也在下方。
 * 只翻行，列不动。
 */
export function flipRow(r: number, rows: number): number {
  return rows - 1 - r;
}

/** 前线带（两半之间那条）的上下边缘。 */
export function frontBand(L: LzqLayout, rows: number) {
  const top = rows / 2 * L.cellH;
  return { top, height: L.gap };
}
