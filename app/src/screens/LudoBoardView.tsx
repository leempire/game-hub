// 飞行棋棋盘视图：SVG 静态层（赛道/航道/停机坪/中央三角/虚线箭头）+ 棋子动画覆盖层。
//
// 从 LudoScreen 拆出来——对局屏本体已经很长（交互/协议/动画闸门/结算），
// 棋盘美术是自成一体的另一件事。几何换算全在 ../ludoBoard。
import React from 'react';
import { View, StyleSheet, Animated } from 'react-native';
import Svg, { Circle, Rect, Path, Line, Polygon, G } from 'react-native-svg';
import { PlaneGlyph } from '../icons';
import { ludoColors } from '../theme';
import {
  GRID_UNITS, TRACK, HOME, CENTER_PARK, ARROWS, CORNER_CUTS,
  cellRect, cellCenter, unitOf, apronRect, apronSlotPixel, piecePixel,
  trackColorKey, TRACK_COLOR_ORDER, stackOffset,
} from '../ludoBoard';

const PLAYER_COLORS = ludoColors;
const PIECE_EDGE = 'rgba(0,0,0,0.6)';  // 静态棋子描边（黄子靠它成形）
const PIECE_ACTIVE = '#111827';        // 可动棋子的深墨环
// 棋盘：基地留白、赛道只涂色不画格线、格心一个奶油圆（标准飞行棋的观感）。
// 数值取自 tmp/飞行棋.md 的视觉规格。
const BOARD_BG = '#F1F1F1';       // 棋盘底 / 基地空地
const CREAM = '#F2ECCE';          // 赛道与航道的格心圆
// 格心圆的深色描边：亮黄格（#F2D024）压在浅灰底上只有 1.35:1，格子的边界
// 全靠这圈描边成形——标准棋盘的奶油圆本来就有细描边。
const CREAM_EDGE = 'rgba(0,0,0,0.38)';
const APRON_EDGE = '#222222';     // 停机坪黑框
const ARROW_INK = '#1F2937';      // 虚线捷径的深墨垫底（对四色 3.7~11.7:1）
// 危险格（对手 1~6 步内能踩到）：半透明红罩 + 红环。红环 vs 四色赛道格
// 3.0~5.6:1，且「多一圈」本身是形状通道。
const DANGER_FILL = 'rgba(220,38,38,0.22)';
const DANGER_EDGE = '#7F1D1D';    // 深红，压在四色格上都 ≥3:1

// ── 棋子位置换算 ──────────────────────────────────────
// 几何表在 ../ludoBoard（15×15 逻辑格 + 十字环赛道，纯函数、可单测）。

/** 每架飞机的动画值：x/y 存「格心的像素坐标」，直接喂给 transform。 */
export interface PieceAnim {
  x: Animated.Value;
  y: Animated.Value;
  scale: Animated.Value;
}

/** 给定一个座位的 4 架飞机，算出各自应处的像素中心（含同格叠子的错位）。 */
export function pieceCenters(seat: number, pieces: number[], edge: number): [number, number][] {
  const u = edge / GRID_UNITS;
  const pts = pieces.map((prog, pi) => piecePixel(seat, pi, prog, edge));
  const groups = new Map<string, number[]>();
  pts.forEach((p, pi) => {
    const key = `${Math.round(p[0])},${Math.round(p[1])}`;
    const bucket = groups.get(key);
    if (bucket) bucket.push(pi);
    else groups.set(key, [pi]);
  });
  return pts.map((p, pi) => {
    const group = groups.get(`${Math.round(p[0])},${Math.round(p[1])}`)!;
    const [ox, oy] = stackOffset(group.indexOf(pi), group.length, u);
    return [p[0] + ox, p[1] + oy] as [number, number];
  });
}


// ── 棋盘 ──────────────────────────────────────────────
// 静态部分（赛道/航道/基地/中央）用 SVG；**棋子用 Animated.View 覆盖层**。
// 为什么棋子不画进 SVG：动画 SVG 的 cx/cy 必须 useNativeDriver:false +
// Animated.createAnimatedComponent(Circle)，既慢又和项目其它动画写法不一致；
// 换成绝对定位的 Animated.View 后位移走 transform，能用原生驱动。
export default function LudoBoard({
  gameState,
  movable,
  dangerCells,
  onPiecePress,
  edge,
  anims,
  mySeat,
}: {
  gameState: any;
  movable: number[];
  /** 对手 1~6 步内能踩到的全局赛道格下标（只在自己回合非空）。 */
  dangerCells: Set<number>;
  onPiecePress: (pieceId: number) => void;
  edge: number;
  anims: Map<string, PieceAnim>;
  /** 自己的座位号：用来把「属于自己那条虚线」画得可辨认（见下方 ARROWS）。 */
  mySeat?: number;
}) {
  if (!gameState) return null;

  const u = edge / GRID_UNITS;   // 一个「单位」的像素边长（内圈格 = 1 单位）
  const players = gameState.players || {};
  const seats = Object.keys(players).map((s) => parseInt(s, 10));
  const colorOf = (seat: number) => PLAYER_COLORS[players[String(seat)]?.color] || '#999';
  // 棋盘是**固定美术**：4 个基地/4 条航道/中央四色三角永远都在，2 人局也一样——
  // 赛道本来就画满 4 色循环，只画参赛的两色会让半个棋盘空白得像没渲染出来。
  // 座位色优先取对局状态（万一服务端改了配色也跟得上），该座位没人时退回固定色表。
  const artColor = (seat: number) =>
    PLAYER_COLORS[players[String(seat)]?.color] || PLAYER_COLORS[TRACK_COLOR_ORDER[seat]];
  const ALL_SEATS = [0, 1, 2, 3];
  const myColor = mySeat == null ? '' : players[String(mySeat)]?.color;
  const g = (i: number) => unitOf(i) * u;

  const pieceNodes: React.ReactElement[] = [];
  seats.forEach((seat) => {
    const pieces: number[] = players[String(seat)]?.pieces || [];
    pieces.forEach((_progress, pi) => {
      const anim = anims.get(`${seat}-${pi}`);
      if (!anim) return;
      const isMovable = movable.includes(pi) && seat === (gameState.current_seat ?? -1);
      // 可动棋子大一圈（第二通道），并额外套一圈白环 + 半透明墨底高亮
      const d = u * (isMovable ? 0.80 : 0.72);
      const halo = d * 1.5;
      pieceNodes.push(
        <Animated.View
          key={`p${seat}-${pi}`}
          testID={`ludo-piece-${seat}-${pi}`}
          onStartShouldSetResponder={() => isMovable}
          onResponderRelease={() => { if (isMovable) onPiecePress(pi); }}
          style={[
            styles.piece,
            {
              left: -d / 2, top: -d / 2, width: d, height: d,
              transform: [
                { translateX: anim.x }, { translateY: anim.y }, { scale: anim.scale },
              ],
            },
          ]}
        >
          {/* 可动高亮：白环 + 半透明墨底垫。
              白环在浅灰底/奶油圆上看不见、墨底垫在深色格上看不见——两者叠起来
              对四种棋色、赛道四色、基地浅灰三种底都成立。加上「大一圈」，
              形状是第二通道（WCAG 1.4.1：颜色不能是唯一信息通道）。
              只给服务端 movable 列表里的棋子上高亮：停机坪的飞机仅在掷出 6 时
              才进这个列表，已到终点的飞机永远不进（引擎 _get_movable_pieces 保证） */}
          {isMovable && (
            <View
              testID={`ludo-halo-${seat}-${pi}`}
              pointerEvents="none"
              style={[styles.halo, {
                left: -(halo - d) / 2, top: -(halo - d) / 2,
                width: halo, height: halo, borderRadius: halo / 2,
              }]}
            />
          )}
          <View style={[styles.disc, {
            width: d, height: d, borderRadius: d / 2,
            backgroundColor: colorOf(seat),
            borderWidth: isMovable ? 2.5 : 1.5,
            borderColor: isMovable ? PIECE_ACTIVE : PIECE_EDGE,
          }]}>
            {/* 飞机图案：白色剪影 + 深墨描边。棋子落在同色格上时颜色分不开，
                靠图案与描边区分（WCAG 1.4.1） */}
            <PlaneGlyph size={d * 0.68} />
          </View>
        </Animated.View>,
      );
    });
  });

  // 中央 3×3（第 6–8 行/列）被两条对角线切成 4 个大三角，箭头尖全部指向
  // 棋盘正中心，每个三角与同色的臂相连（座位 s 的臂 = HOME[s]）。
  const MID = 7;   // 中央 3×3 的中心格
  const TRI: Record<number, string> = {
    0: `M${g(6)},${g(6)} L${g(6)},${g(9)} L${g(MID + 0.5)},${g(MID + 0.5)} Z`,   // 左
    1: `M${g(6)},${g(6)} L${g(9)},${g(6)} L${g(MID + 0.5)},${g(MID + 0.5)} Z`,   // 上
    2: `M${g(9)},${g(6)} L${g(9)},${g(9)} L${g(MID + 0.5)},${g(MID + 0.5)} Z`,   // 右
    3: `M${g(6)},${g(9)} L${g(9)},${g(9)} L${g(MID + 0.5)},${g(MID + 0.5)} Z`,   // 下
  };

  return (
    <View style={{ width: edge, height: edge }}>
      <Svg width={edge} height={edge} viewBox={`0 0 ${edge} ${edge}`}>
        <Rect x={0} y={0} width={edge} height={edge} rx={u * 0.7} fill={BOARD_BG} />

        {/* 公共赛道 52 格：蓝→绿→红→黄 四色循环（每色 13 格）。
            这是标准飞行棋棋盘的底色，也是「同色跳格」规则的视觉依据——
            玩家看到自己的颜色就知道能跳。不画格线，只靠色块与格心奶油圆区分。
            **外圈格宽 2 倍**（第 0/14 行/列），所以十字的四条边看起来厚重、
            四角基地留白——见 ludoBoard.unitOf。 */}
        {TRACK.map(([r, c], i) => {
          const rect = cellRect(r, c, edge);
          const [cx, cy] = cellCenter(r, c, edge);
          const rad = Math.min(rect.w, rect.h) * 0.26;
          const danger = dangerCells.has(i);
          return (
            <G key={`t${i}`}>
              <Rect testID={`ludo-cell-${i}`} x={rect.x} y={rect.y} width={rect.w} height={rect.h}
                fill={PLAYER_COLORS[trackColorKey(i)] || '#999'} />
              <Circle cx={cx} cy={cy} r={rad}
                fill={CREAM} stroke={CREAM_EDGE} strokeWidth={Math.max(0.6, u * 0.035)} />
              {/* 危险格：对手 1~6 步内能踩到。半透明红罩 + 格心圆外套一圈红环——
                  环是第二通道，色觉障碍用户靠「这个格子多了一圈」也能认出来
                  （WCAG 1.4.1）。只在自己回合画。 */}
              {danger && (
                <>
                  <Rect x={rect.x} y={rect.y} width={rect.w} height={rect.h}
                    fill={DANGER_FILL} />
                  <Circle cx={cx} cy={cy} r={rad * 1.45}
                    fill="none" stroke={DANGER_EDGE} strokeWidth={Math.max(1, u * 0.07)} />
                </>
              )}
            </G>
          );
        })}

        {/* 赛道四个凹角是斜切（如 (4,3) → (3,4) 共角不共边）。被「切掉」的那一格
            按对角线分成两半，各取转弯前后那一格的颜色——环看起来就是绕过这个角
            折进去的，而不是缺了一块。 */}
        {CORNER_CUTS.map(([cr, cc], k) => {
          const rect = cellRect(cr, cc, edge);
          const bi = (k * 13 + 2) % 52;   // 转弯前那一格（沿行进方向先到）
          const ai = (k * 13 + 3) % 52;   // 转弯后那一格
          const x0 = rect.x, y0 = rect.y, x1 = rect.x + rect.w, y1 = rect.y + rect.h;
          // 四个半格三角（按左上 / 右上 / 左下 / 右下编号）。切角按 90° 旋转对称：
          // 都是「行→列」的直角弯，转弯前那一格（before）依次在切角的西 / 北 /
          // 东 / 南侧，占靠着它的那半边。**两侧的三角要对调**——只按 k%2 取同一边
          // 会把右半张棋盘的两个切角颜色画反（蓝方视角看右上/右下凹槽会反色）。
          const TRI_HALF: ReadonlyArray<string> = [
            `${x0},${y0} ${x1},${y0} ${x0},${y1}`,   // 0 左上（╱ 的左上）
            `${x0},${y0} ${x1},${y0} ${x1},${y1}`,   // 1 右上（╲ 的右上）
            `${x0},${y0} ${x0},${y1} ${x1},${y1}`,   // 2 左下（╲ 的左下）
            `${x1},${y0} ${x0},${y1} ${x1},${y1}`,   // 3 右下（╱ 的右下）
          ];
          // k: [after 占哪半, before 占哪半]
          const CUT_HALVES: ReadonlyArray<[number, number]> = [
            [1, 2],   // 切角 (4,4)：before 在西 → 左下；after 在北 → 右上
            [3, 0],   // 切角 (4,10)：before 在北 → 左上；after 在东 → 右下
            [2, 1],   // 切角 (10,10)：before 在东 → 右上；after 在南 → 左下
            [0, 3],   // 切角 (10,4)：before 在南 → 右下；after 在西 → 左上
          ];
          const [afterHalf, beforeHalf] = CUT_HALVES[k % 4];
          return (
            <G key={`cut${k}`}>
              <Polygon points={TRI_HALF[afterHalf]} fill={PLAYER_COLORS[trackColorKey(ai)] || '#999'} />
              <Polygon points={TRI_HALF[beforeHalf]} fill={PLAYER_COLORS[trackColorKey(bi)] || '#999'} />
            </G>
          );
        })}

        {/* 终点航道：每色 6 格连成一条纯色带，格心奶油圆 */}
        {ALL_SEATS.map((seat) => HOME[seat].map(([r, c], h) => {
          const rect = cellRect(r, c, edge);
          const [cx, cy] = cellCenter(r, c, edge);
          return (
            <G key={`h${seat}-${h}`}>
              <Rect x={rect.x} y={rect.y} width={rect.w} height={rect.h} fill={artColor(seat)} />
              <Circle cx={cx} cy={cy} r={Math.min(rect.w, rect.h) * 0.26}
                fill={CREAM} stroke={CREAM_EDGE} strokeWidth={Math.max(0.6, u * 0.035)} />
            </G>
          );
        }))}

        {/* 中央四色三角：每个三角一个奶油圆（位置就是该座位已到达飞机的停靠点） */}
        {ALL_SEATS.map((seat) => {
          const [pr, pc] = CENTER_PARK[seat];
          const [cx, cy] = cellCenter(pr, pc, edge);
          return (
            <G key={`c${seat}`}>
              <Path d={TRI[seat]} fill={artColor(seat)} />
              <Circle cx={cx} cy={cy} r={u * 0.28} fill={CREAM} stroke={CREAM_EDGE} strokeWidth={Math.max(0.6, u * 0.035)} />
            </G>
          );
        })}

        {/* 虚线捷径：四条，各从一条臂根部的同色格飞 12 格到另一个同色格。
            画在十字四角内侧的空白走廊里，横穿本臂根部——所以四条构成一组
            旋转对称的箭头。同色短划线 + 深墨垫底：箭头会横穿赛道格、航道格和
            基地浅灰三种底，单色描边总有一种底看不清。 */}
        {ARROWS.map(({ fromIndex, from, to, color }, k) => {
          const rf = cellRect(from[0], from[1], edge);
          const rt = cellRect(to[0], to[1], edge);
          const [fcx, fcy] = cellCenter(from[0], from[1], edge);
          const [tcx, tcy] = cellCenter(to[0], to[1], edge);
          const dx = tcx - fcx;
          const dy = tcy - fcy;
          const len = Math.hypot(dx, dy) || 1;
          const ux = dx / len;
          const uy = dy / len;
          // 两端各收进自己那一格，箭头画在两格之间的空白里（与参考图一致）
          const x1 = fcx + ux * (Math.abs(ux) * rf.w + Math.abs(uy) * rf.h) / 2;
          const y1 = fcy + uy * (Math.abs(ux) * rf.w + Math.abs(uy) * rf.h) / 2;
          const x2 = tcx - ux * (Math.abs(ux) * rt.w + Math.abs(uy) * rt.h) / 2;
          const y2 = tcy - uy * (Math.abs(ux) * rt.w + Math.abs(uy) * rt.h) / 2;
          const tipX = x2;
          const tipY = y2;
          const head = u * 0.42;
          const nx = -uy;
          const ny = ux;
          const headPts = [
            `${tipX},${tipY}`,
            `${tipX - ux * head + nx * head * 0.75},${tipY - uy * head + ny * head * 0.75}`,
            `${tipX - ux * head - nx * head * 0.75},${tipY - uy * head - ny * head * 0.75}`,
          ].join(' ');
          const dash = `${u * 0.26} ${u * 0.18}`;
          const line = PLAYER_COLORS[color] || '#999';
          // 四条虚线一色一条、各归一个座位，**只有自己颜色的那条能飞**（见
          // ludo.py::is_arrow）。所以给自己那条的起点套一圈靶环，一眼能认出
          // 哪条是自己的——光看颜色要先把「箭头色 = 座位色」这层映射想一遍。
          const mine = !!myColor && color === myColor;
          // 三层描边：深墨垫底 → 奶油白 → 本色虚线。
          // 只画「白垫 + 本色」时，本色可能与它横穿的那条臂撞色（绿箭头压红臂只有
          // 1.6:1），小屏上根本看不出这里有捷径。加一层深墨垫底后，无论压在哪种
          // 底色上，箭头轮廓都 ≥3:1（深墨 vs 四色 3.7~11.7:1）。
          return (
            <G key={`arrow-${k}`} testID={`ludo-arrow-${fromIndex}`}>
              <Line x1={x1} y1={y1} x2={tipX} y2={tipY}
                stroke={ARROW_INK} strokeWidth={u * 0.38} strokeDasharray={dash} />
              <Line x1={x1} y1={y1} x2={tipX} y2={tipY}
                stroke={CREAM} strokeWidth={u * 0.30} strokeDasharray={dash} />
              <Line x1={x1} y1={y1} x2={tipX} y2={tipY}
                stroke={line} strokeWidth={u * 0.22} strokeDasharray={dash} />
              <Polygon points={headPts} fill={line} stroke={ARROW_INK} strokeWidth={u * 0.06} />
              <Circle cx={x1} cy={y1} r={u * 0.15}
                fill={line} stroke={ARROW_INK} strokeWidth={u * 0.08} />
              {/* 自己那条虚线再套一圈「靶环」。**不能靠填充色区分**——虚线起点
                  格本来就是自己颜色，同色填充等于没填（第一版就是这么写的，
                  实心圆完全看不见）。多一圈是形状通道（WCAG 1.4.1）。 */}
              {mine && (
                <Circle cx={x1} cy={y1} r={u * 0.27} fill="none"
                  stroke={ARROW_INK} strokeWidth={u * 0.07} />
              )}
            </G>
          );
        })}

        {/* 四角停机坪：贴棋盘外角的小黑框方框 + 2×2 停机位。
            基地其余部分是棋盘底色（浅灰空地），**不画格线也不涂色**——原来整块
            6×6 涂成座位色又重又挤，是「棋盘太紧凑太丑」的主因。 */}
        {ALL_SEATS.map((seat) => {
          const ap = apronRect(seat, edge);
          return (
            <G key={`base-${seat}`}>
              <Rect x={ap.x} y={ap.y} width={ap.size} height={ap.size}
                fill="none" stroke={APRON_EDGE} strokeWidth={Math.max(1, u * 0.06)}
                rx={u * 0.12} />
              {[0, 1, 2, 3].map((k) => {
                const [cx, cy] = apronSlotPixel(seat, k, edge);
                return (
                  <Circle key={`s${k}`} cx={cx} cy={cy} r={ap.size * 0.16}
                    fill="#FFFFFF" stroke={APRON_EDGE} strokeWidth={Math.max(1, u * 0.05)} />
                );
              })}
            </G>
          );
        })}
      </Svg>

      {/* 棋子层：必须在 Svg 之后才画在上层；box-none 让空白处的触摸穿透到棋盘 */}
      <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
        {pieceNodes}
      </View>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────
const styles = StyleSheet.create({
  // 棋子：以 (0,0) 为圆心，靠 transform 平移到格心
  piece: { position: 'absolute' },
  disc: { alignItems: 'center', justifyContent: 'center' },
  halo: {
    position: 'absolute',
    backgroundColor: 'rgba(17,24,39,0.30)',
    borderWidth: 2.5, borderColor: '#FFFFFF',
  },
});
