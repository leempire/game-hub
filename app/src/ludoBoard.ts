// 飞行棋棋盘几何：15×15 逻辑格 + 「围着十字边缘一圈」的 52 格赛道。
//
// 引擎（server/app/ludo.py）用抽象的 progress 表示棋子位置，与视觉无关：
//   -1       基地
//   0..48    公共赛道（玩家相对；全局格 = (progress + PLAYER_OFFSETS[seat]) % 52）
//   49..54   自己的终点航道（外 → 内，6 格）
//   55       已到达（停在中圈自己的三角里）
// 本模块把 progress 映射到 15×15 网格，并给出走子的逐格路径（供动画用）。
//
// 棋盘结构（对齐 tmp/飞行棋.png，视觉真值见 tmp/飞行棋.md）：
//   - **外圈（第 0/14 行/列）宽 2 倍**：全盘 = 2 + 13 + 2 = 17 个单位
//   - 赛道是「围着十字边缘一圈」的环形：十字的四条边贴着棋盘外缘走，
//     四个凹角向里切进去（斜切，见 TRACK 的注释），52 格 = 4 × 13
//   - 四条臂是各方终点航道（上臂绿 / 右臂红 / 下臂黄 / 左臂蓝）
//   - 四角基地 + 外角黑框停机坪；中圈 3×3 被对角线切成四个三角
//
// 配色与虚线捷径的常量**必须与 server/app/ludo.py 保持一致**（那边是权威，
// 走子裁决在服务端；这边只负责把同一套规则画出来）。

export const GRID = 15;
/** 赛道格数（配色循环、全局坐标取模都按它）。飞机自己只走 0..RING_END。 */
export const RING = 52;
/** 相邻两条航道入口之间的格距（= 四条起跑线的间距），RING / 4。 */
export const QUADRANT = RING / 4; // 13
/** 自己在赛道上能走的最后一格 = 自己航道入口（相对进度）。 */
export const RING_END = 48;
export const HOME_STRETCH = 6;
/** 航道 49..54 共 6 格，走完即到达（55 停在中圈三角里，不占航道格）。 */
export const FINISH_PROGRESS = RING_END + HOME_STRETCH + 1; // 55
export const PIECES_PER_PLAYER = 4;
export const PLAYER_OFFSETS = [0, 13, 26, 39];

/**
 * 赛道配色循环：蓝→绿→红→黄（= 座位序），每 4 格一轮，52 格 = 13 轮。
 *
 * 起跑线偏移 13 ≡ 1 (mod 4)，所以四条起跑线正好落在循环的四个不同颜色上——
 * **每个座位的起飞点天然就是自己的颜色**，而「相对进度是 4 的倍数」⇔
 * 「踩在自己颜色上」对任何座位都成立。改偏移或改配色顺序都会破坏这个不变式
 * （引擎侧有 test_ludo.py::TestTrackColors 兜底）。
 */
export const TRACK_COLOR_ORDER = ['blue', 'green', 'red', 'yellow'] as const;

/** TRACK 第 index 格（绝对索引）的颜色 key，对应 theme.ludoColors。 */
export function trackColorKey(index: number): string {
  return TRACK_COLOR_ORDER[((index % 4) + 4) % 4];
}

/**
 * 公共赛道 52 格（绝对索引 = 座位 0 的相对进度）。
 *
 * 形状是「围着十字边缘一圈」：沿棋盘外缘走（第 0/14 行、第 0/14 列），
 * 到四个凹角处向里切到第 4/10 行/列再折回——所以环看起来是一个十字轮廓。
 * 四个凹角是**斜切**（如 (4,3) → (3,4) 共角而非共边），这是 4×13=52 的固有
 * 结果。**不要**为了正交补连接格（补了就变 56 格，与引擎的 52 不符）。
 *
 * 起点（index 0）在 (4,1)，是蓝方起飞点；顺时针绕行。座位 s 的起点 =
 * TRACK[PLAYER_OFFSETS[s]]，正好是自己的颜色（见 TRACK_COLOR_ORDER 注释）。
 */
export const TRACK: ReadonlyArray<readonly [number, number]> = [
  [4, 1], [4, 2], [4, 3], [3, 4], [2, 4], [1, 4], [0, 4], [0, 5], [0, 6], [0, 7],
  [0, 8], [0, 9], [0, 10], [1, 10], [2, 10], [3, 10], [4, 11], [4, 12], [4, 13], [4, 14],
  [5, 14], [6, 14], [7, 14], [8, 14], [9, 14], [10, 14], [10, 13], [10, 12], [10, 11], [11, 10],
  [12, 10], [13, 10], [14, 10], [14, 9], [14, 8], [14, 7], [14, 6], [14, 5], [14, 4], [13, 4],
  [12, 4], [11, 4], [10, 3], [10, 2], [10, 1], [10, 0], [9, 0], [8, 0], [7, 0], [6, 0],
  [5, 0], [4, 0],
];

/** 赛道四个凹角（斜切处被「切掉」的格）——画转弯三角用。 */
export const CORNER_CUTS: ReadonlyArray<readonly [number, number]> = [
  [4, 4], [4, 10], [10, 10], [10, 4],
];

/** 每色终点航道 6 格，索引 0 = 最外（progress 49），5 = 最内（progress 54）。 */
export const HOME: ReadonlyArray<ReadonlyArray<readonly [number, number]>> = [
  [[7, 1], [7, 2], [7, 3], [7, 4], [7, 5], [7, 6]],       // 座位0 蓝：左臂，从左边进
  [[1, 7], [2, 7], [3, 7], [4, 7], [5, 7], [6, 7]],       // 座位1 绿：上臂，从上边进
  [[7, 13], [7, 12], [7, 11], [7, 10], [7, 9], [7, 8]],   // 座位2 红：右臂，从右边进
  [[13, 7], [12, 7], [11, 7], [10, 7], [9, 7], [8, 7]],   // 座位3 黄：下臂，从下边进
];

/** 四角基地象限的左上角（与座位↔颜色一一对应：左上蓝/右上绿/右下红/左下黄）。 */
export const BASE_ORIGIN: ReadonlyArray<readonly [number, number]> = [
  [0, 0], [0, 9], [9, 9], [9, 0],
];

/**
 * 已到达的棋子停靠点：中圈 3×3 被两条对角线切成四个三角，每个三角一个停靠点
 * （坐标可以带小数，三角重心不在格心上）。左三角蓝 / 上三角绿 / 右三角红 /
 * 下三角黄，与四条臂同向。
 */
export const CENTER_PARK: ReadonlyArray<readonly [number, number]> = [
  [7.5, 6.5],   // 座位0 蓝：左三角
  [6.5, 7.5],   // 座位1 绿：上三角
  [7.5, 8.5],   // 座位2 红：右三角
  [8.5, 7.5],   // 座位3 黄：下三角
];

// ── 像素映射 ──────────────────────────────────────────
// 逻辑上仍是 15×15，但**外圈（第 0/14 行/列）宽 2 倍**——这是标准飞行棋棋盘
// 的观感：外圈格厚重、基地留白。总宽 = 2 + 13 + 2 = 17 个单位。
export const GRID_UNITS = 17;

/** 第 i 条网格线（0..15，可带小数）落在第几个单位上。 */
export function unitOf(i: number): number {
  if (i <= 0) return 0;
  if (i >= GRID) return GRID_UNITS;
  if (i < 1) return i * 2;
  if (i <= GRID - 1) return 2 + (i - 1);
  return GRID_UNITS - (GRID - i) * 2;
}

/** 逻辑格 (row, col)（可带小数）的像素矩形。 */
export function cellRect(
  row: number, col: number, edge: number,
): { x: number; y: number; w: number; h: number } {
  const u = edge / GRID_UNITS;
  const x = unitOf(col) * u;
  const y = unitOf(row) * u;
  return { x, y, w: (unitOf(col + 1) - unitOf(col)) * u, h: (unitOf(row + 1) - unitOf(row)) * u };
}

/** 逻辑格 (row, col)（可带小数）的像素中心。 */
export function cellCenter(row: number, col: number, edge: number): [number, number] {
  const r = cellRect(row, col, edge);
  return [r.x + r.w / 2, r.y + r.h / 2];
}

// ── 停机坪（基地外角的小方框）────────────────────────
// 贴棋盘外角、边长约 3.7 个单位；2×2 四个停机位。基地其余部分是浅灰空地，
// 不画格线。位置按「棋盘边长的比例」定义，保证与 unitOf 的缩放一致。
export const APRON_INSET_U = 0.14;   // 距棋盘边缘（单位）
export const APRON_SIZE_U = 3.7;     // 停机坪边长（单位）
export const APRON_SLOT = 0.27;      // 停机位在停机坪内的相对位置（0.27 / 0.73）

/** 停机坪方框的像素矩形（按座位取外角方向）。 */
export function apronRect(seat: number, edge: number) {
  const u = edge / GRID_UNITS;
  const inset = APRON_INSET_U * u;
  const size = APRON_SIZE_U * u;
  const right = seat === 1 || seat === 2;
  const bottom = seat === 2 || seat === 3;
  return {
    x: right ? edge - inset - size : inset,
    y: bottom ? edge - inset - size : inset,
    size,
  };
}

/** 停机位的像素中心（seat 的基地里第 pieceId 个位子）。 */
export function apronSlotPixel(seat: number, pieceId: number, edge: number): [number, number] {
  const { x, y, size } = apronRect(seat, edge);
  const a = size * APRON_SLOT;
  const b = size * (1 - APRON_SLOT);
  const col = pieceId % 2;
  const row = Math.floor(pieceId / 2);
  return [x + (col === 0 ? a : b), y + (row === 0 ? a : b)];
}

/** 棋子在给定 progress 下的像素中心（基地里的飞机落在停机位上，不走网格）。 */
export function piecePixel(seat: number, pieceId: number, progress: number, edge: number): [number, number] {
  if (progress < 0) return apronSlotPixel(seat, pieceId, edge);
  const [r, c] = cellFor(seat, pieceId, progress);
  return cellCenter(r, c, edge);
}

// ── 虚线捷径 ──────────────────────────────────────────
/** 虚线起点在相对进度 3 / 16 / 29 / 42（每 13 格一条），飞 ARROW_STEP = 12 格。 */
export const ARROW_FIRST = 3;
export const ARROW_STEP = 12;

/** 相对进度 → 全局赛道索引（仅赛道段有效）。 */
export function globalIndex(seat: number, progress: number): number {
  return (progress + PLAYER_OFFSETS[seat]) % RING;
}

export interface ArrowSpec {
  /** 起点格的绝对赛道索引。 */
  fromIndex: number;
  /** 终点格的绝对赛道索引。 */
  toIndex: number;
  from: readonly [number, number];
  to: readonly [number, number];
  /** 本条虚线对哪个座位是「自己颜色」（四条一色一条，用于配色）。 */
  color: string;
}

/**
 * 四条虚线捷径（绝对赛道索引 3→15 / 16→28 / 29→41 / 42→2）。
 *
 * 起终点同色、相隔 12 格；画在十字四角内侧的空白走廊里，横穿本臂根部，
 * 所以四条构成旋转对称的一组箭头（与标准棋盘一致）。
 * 最后一条（42→2）对任何座位都会飞过自己的航道入口，规则上不生效——
 * 引擎侧 arrow_target() 返回 -1，这里照样画出来（它是棋盘固定涂装）。
 */
export const ARROWS: ReadonlyArray<ArrowSpec> = [0, 1, 2, 3].map((k) => {
  const fromIndex = ARROW_FIRST + k * QUADRANT;
  const toIndex = (fromIndex + ARROW_STEP) % RING;
  return {
    fromIndex,
    toIndex,
    from: TRACK[fromIndex],
    to: TRACK[toIndex],
    color: trackColorKey(fromIndex),
  };
});

/** progress → 15×15 网格坐标（row, col），col/row 可能带小数（中圈停靠点）。 */
export function cellFor(seat: number, pieceId: number, progress: number): readonly [number, number] {
  if (progress < 0) return BASE_ORIGIN[seat] ?? BASE_ORIGIN[0];
  if (progress >= FINISH_PROGRESS) return CENTER_PARK[seat] ?? CENTER_PARK[0];
  if (progress > RING_END) {
    const stretch = HOME[seat] ?? HOME[0];
    return stretch[Math.min(progress - RING_END - 1, stretch.length - 1)];
  }
  return TRACK[globalIndex(seat, progress)];
}

/**
 * 一次走子经过的 progress 序列（不含起点，含终点）——**仅作兜底**。
 *
 * 自从加了同色跳格与虚线飞行，真实路径的形状只有服务端知道（跳/飞各最多一次、
 * 顺序还取决于落点），`piece_moved` 会带 `path`，客户端应优先用它。
 * 这个函数只按「掷骰子逐格走」推算，服务端没给 path 时用来兜底：
 * 正常前进逐格走；掷出 6 起飞是基地 → 起点一步；超出 55 时引擎把落点折返
 * （new = 55 - (old+dice-55)），视觉上要先前推到 55 再折回。
 */
export function progressPath(from: number, to: number, dice: number): number[] {
  if (from < 0) return [0];
  if (from + dice > FINISH_PROGRESS) {
    const out: number[] = [];
    for (let p = from + 1; p <= FINISH_PROGRESS; p++) out.push(p);
    for (let p = FINISH_PROGRESS - 1; p >= to; p--) out.push(p);
    return out;
  }
  const out: number[] = [];
  for (let p = from + 1; p <= to; p++) out.push(p);
  return out;
}

/** 逐格动画的单步时长：长距离别拖沓，短距离别瞬移。 */
export function stepDuration(steps: number): number {
  if (steps <= 1) return 115;
  return Math.max(55, Math.min(115, Math.round(600 / steps)));
}

/**
 * 同格多子时的错位偏移（像素）。4 架叠在一格里只看得见一架，必须散开。
 * 返回该格第 index 个子（共 total 个）相对格心的偏移。
 */
export function stackOffset(index: number, total: number, cell: number): [number, number] {
  if (total <= 1) return [0, 0];
  const angle = (index / total) * Math.PI * 2 - Math.PI / 2;
  const r = cell * 0.20;
  return [Math.cos(angle) * r, Math.sin(angle) * r];
}
