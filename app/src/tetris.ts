/**
 * 俄罗斯方块客户端：本地预演（服务端仍是权威）。
 *
 * 玩法是「本地即时响应 + 每块方块一次往返」：重力、左右、旋转都在本地算，攒成
 * 一串动作；**方块锁定**时把这串动作一次性发给服务端，服务端用自己的引擎重放并
 * 回一份权威盘面。因此本地这份只是「预演」，唯一的作用是让手感不等网络。
 *
 * **形状与踢墙顺序全部来自服务端**（`sync`/`game_start`/`tetris_state` 的 `shapes`
 * 与 `kicks`），这里一格规则都不重写——象棋的合法着法、飞行棋的走子路径都是
 * 「客户端自己推 → 画错」的老教训。
 */

export interface TetrisPiece {
  piece: string;
  rotation: number;
  x: number;
  y: number;
}

export interface TetrisRules {
  /** 方块名 → 四个旋转态，每态是 `[dy, dx]` 相对坐标列表。 */
  shapes: Record<string, number[][][]>;
  /** 旋转踢墙时依次尝试的 x 偏移（与服务端同序）。 */
  kicks: number[];
}

export interface TetrisLeaderRow {
  user_id: number;
  color: string;
  score: number;
  lines: number;
  level: number;
  finished: boolean;
  finish_reason: string | null;
}

/** 消行数 → 送给对手的攻击行数（展示用文案；裁决在服务端）。 */
export const GARBAGE_LABEL: Record<number, string> = {
  1: '消除 1 行',
  2: '消除 2 行 · 顶对手 1 行',
  3: '消除 3 行 · 顶对手 2 行',
  4: '四连消 · 顶对手 4 行',
};

/** 每消 10 行升一级（与服务端 `LINES_PER_LEVEL` 一致，只用于展示与降速）。 */
export const LINES_PER_LEVEL = 10;

/**
 * 重力间隔：等级越高掉得越快。
 *
 * 这是**纯客户端节奏**（服务端只重放动作、不校验时间），所以它不是规则，
 * 不必也不该由服务端下发。
 */
export function gravityMs(level: number): number {
  const table = [800, 720, 630, 550, 470, 380, 300, 220, 160, 120];
  const idx = Math.max(0, Math.min(table.length - 1, Math.floor(level) - 1));
  return table[idx];
}

/** 某方块在某姿态下的盘面绝对坐标 `[row, col]`。 */
export function cellsOf(
  rules: TetrisRules,
  piece: string,
  rotation: number,
  x: number,
  y: number,
): number[][] {
  const states = rules.shapes[piece];
  if (!states) return [];
  const state = states[((rotation % 4) + 4) % 4];
  return state.map(([dy, dx]) => [y + dy, x + dx]);
}

/** 该摆放是否合法：不越界、不压到已有方块。 */
export function isValid(
  board: number[][],
  rules: TetrisRules,
  piece: string,
  rotation: number,
  x: number,
  y: number,
): boolean {
  const rows = board.length;
  const cols = rows > 0 ? board[0].length : 0;
  for (const [r, c] of cellsOf(rules, piece, rotation, x, y)) {
    if (c < 0 || c >= cols || r < 0 || r >= rows) return false;
    if (board[r][c] !== 0) return false;
  }
  return true;
}

/** 左右移动；移不动就原样返回（与服务端一样是静默 no-op）。 */
export function movePiece(
  board: number[][],
  rules: TetrisRules,
  cur: TetrisPiece,
  dx: number,
): TetrisPiece {
  const next = { ...cur, x: cur.x + dx };
  return isValid(board, rules, next.piece, next.rotation, next.x, next.y) ? next : cur;
}

/**
 * 旋转（带踢墙）：先试原位，再按服务端的 `kicks` 顺序左右挪。
 *
 * 顺序必须与服务端一致——贴墙旋转时两边的落点不同，预演就会与重放结果分叉。
 */
export function rotatePiece(
  board: number[][],
  rules: TetrisRules,
  cur: TetrisPiece,
  dir: number,
): TetrisPiece {
  const rotation = (((cur.rotation + dir) % 4) + 4) % 4;
  for (const kick of rules.kicks) {
    if (isValid(board, rules, cur.piece, rotation, cur.x + kick, cur.y)) {
      return { ...cur, rotation, x: cur.x + kick };
    }
  }
  return cur;
}

/** 当前方块能往下掉几格（硬降用）。 */
export function dropDistance(
  board: number[][],
  rules: TetrisRules,
  cur: TetrisPiece,
): number {
  let d = 0;
  while (isValid(board, rules, cur.piece, cur.rotation, cur.x, cur.y + d + 1)) d += 1;
  return d;
}

/** 下移一格；掉不动返回 null（调用方据此判定「该锁定了」）。 */
export function stepDown(
  board: number[][],
  rules: TetrisRules,
  cur: TetrisPiece,
): TetrisPiece | null {
  return isValid(board, rules, cur.piece, cur.rotation, cur.x, cur.y + 1)
    ? { ...cur, y: cur.y + 1 }
    : null;
}

/** 攻击行改变盘面后，按原顺序重放尚未上报的输入；首次落定即截断，防止误动下一块。 */
export function replayPending(
  board: number[][],
  rules: TetrisRules,
  current: TetrisPiece,
  pending: string[],
): { current: TetrisPiece; actions: string[]; locked: boolean } {
  let cur = current;
  for (let i = 0; i < pending.length; i += 1) {
    const action = pending[i];
    if (action === 'left' || action === 'right') {
      cur = movePiece(board, rules, cur, action === 'left' ? -1 : 1);
    } else if (action === 'rotate_cw' || action === 'rotate_ccw') {
      cur = rotatePiece(board, rules, cur, action === 'rotate_cw' ? 1 : -1);
    } else if (action === 'down') {
      const next = stepDown(board, rules, cur);
      if (!next) {
        return { current: cur, actions: pending.slice(0, i + 1), locked: true };
      }
      cur = next;
    }
  }
  return { current: cur, actions: pending, locked: false };
}

/** 把当前方块写进盘面（本地预演用；权威结果仍以服务端回包为准）。 */
export function stamp(
  board: number[][],
  rules: TetrisRules,
  cur: TetrisPiece,
): number[][] {
  const next = board.map((row) => row.slice());
  const id = PIECE_IDS[cur.piece] ?? 8;
  for (const [r, c] of cellsOf(rules, cur.piece, cur.rotation, cur.x, cur.y)) {
    if (r >= 0 && r < next.length && c >= 0 && c < next[0].length) next[r][c] = id;
  }
  return next;
}

/**
 * 方块名 → 格子 id（与服务端 `PIECE_IDS` 一致）。
 *
 * 服务端盘面里存的就是这个 id，渲染按 id 上色，所以映射必须两边一致。
 * 顺序与 `app/tetris.py` 的 `"IOT SZJL".replace(" ", "")` 相同。
 */
export const PIECE_IDS: Record<string, number> = {
  I: 1, O: 2, T: 3, S: 4, Z: 5, J: 6, L: 7,
};

/** 格子 id → 方块名（渲染战绩快照与本地盘面时用）。 */
export const ID_TO_PIECE: Record<number, string> = {
  1: 'I', 2: 'O', 3: 'T', 4: 'S', 5: 'Z', 6: 'J', 7: 'L',
};

/** 攻击行填充块的 id（与服务端 `GARBAGE_ID` 一致）。 */
export const GARBAGE_ID = 8;