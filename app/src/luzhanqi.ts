/**
 * 军棋的**展示层**解析：棋子码 ↔ 中文名 / 军衔，以及协议载荷的类型。
 *
 * 这里**一行规则都没有**（同 `uno.ts` 的定位）—— 走法、碰子、合法落点、棋盘地形
 * 全部由服务端下发（地形在 `game.topology` 里，见 `luzhanqiBoard.ts`）。
 */

/** 棋子码（服务端 `luzhanqi.py` 的常量）。 */
export type LzqPiece =
  | 'siling' | 'junzhang' | 'shizhang' | 'lvzhang' | 'tuanzhang'
  | 'yingzhang' | 'lianzhang' | 'paizhang' | 'gongbing'
  | 'dilei' | 'zhadan' | 'junqi';

export const PIECE_NAMES: Record<string, string> = {
  siling: '司令', junzhang: '军长', shizhang: '师长', lvzhang: '旅长',
  tuanzhang: '团长', yingzhang: '营长', lianzhang: '连长', paizhang: '排长',
  gongbing: '工兵', dilei: '地雷', zhadan: '炸弹', junqi: '军旗',
};

/** 军衔值：司令 9 … 工兵 1。炸弹/地雷/军旗不在这个序列里（0）。 */
export const PIECE_RANK: Record<string, number> = {
  siling: 9, junzhang: 8, shizhang: 7, lvzhang: 6, tuanzhang: 5,
  yingzhang: 4, lianzhang: 3, paizhang: 2, gongbing: 1,
};

/** 显示顺序（帮助面板与「我方存活棋子」盘按这个排）。 */
export const PIECE_ORDER: LzqPiece[] = [
  'siling', 'junzhang', 'shizhang', 'lvzhang', 'tuanzhang', 'yingzhang',
  'lianzhang', 'paizhang', 'gongbing', 'zhadan', 'dilei', 'junqi',
];

export function pieceName(code: string | null | undefined): string {
  return code ? PIECE_NAMES[code] ?? code : '？';
}

/** 棋子码 → 两字短名（棋盘格子窄，用完整名会挤）。 */
export function pieceShort(code: string | null | undefined): string {
  return pieceName(code);
}

export type LzqCellKind = 'post' | 'camp' | 'hq';

export interface LzqTopology {
  rows: number;
  cols: number;
  /** rows × cols，元素是 'post' / 'camp' / 'hq'。 */
  kinds: LzqCellKind[][];
  /** 铁路边 `[r1, c1, r2, c2]`（粗线，非工兵只能直走、工兵可拐弯）。 */
  rails: number[][];
  /** 公路边（细线，一步）。 */
  roads: number[][];
  /** 3 条「前线」过界边（可穿过）。 */
  fronts: number[][];
  /** 2 处「山界」（没有边，棋子过不去）。 */
  mountains: number[][];
}

export interface LzqCell {
  /** 'red' | 'black' */
  c: string;
  /** 棋子码；**暗棋下对手的棋子是 null**（服务端压根没下发）。 */
  p: string | null;
}

export interface LzqBattle {
  at: number[];
  attacker: string;
  defender: string;
  outcome: 'attacker_dies' | 'defender_dies' | 'both_die';
  /** 只有「有权知道」的一方才拿得到（自己那枚 / 明棋 / 公开身份模式）。 */
  attacker_piece?: string;
  defender_piece?: string;
  flag_captured?: boolean;
}

export interface LzqView {
  rows: number;
  cols: number;
  cells: (LzqCell | null)[][];
  topology: LzqTopology;
  phase: 'setup' | 'playing';
  mode: 'open' | 'hidden';
  reveal: boolean;
  next: string;
  your_color: string;
  winner: string | null;
  reason: string | null;
  finished: boolean;
  last_move: { from: number[]; to: number[]; by: string } | null;
  flag_revealed: Record<string, boolean>;
  ready: Record<string, boolean>;
  /** 己方每种棋子还剩几枚。 */
  alive: Record<string, number>;
  battles: LzqBattle[];
  /** 合法着法，扁平 `[fr, fc, tr, tc]`；**只算收件人自己的**，不是自己回合时为空。 */
  legal: number[][];
  /** 布阵阶段可用的经典阵型。**名字与顺序都由服务端给**，客户端不抄一份。
   *  只有对局视图带它 —— 战绩回放的快照是终局数据，没有布阵这回事。 */
  presets?: LzqPreset[];
}

export interface LzqPreset {
  key: string;
  label: string;
}

export const SIDE_NAMES: Record<string, string> = { red: '红方', black: '黑方' };

export function sideName(color: string | null | undefined): string {
  return color ? SIDE_NAMES[color] ?? color : '';
}

/** 终局原因文案。 */
export const LZQ_REASON: Record<string, string> = {
  flag: '军旗被夺',
  nomove: '无棋可走',
  surrender: '对方认输',
  disconnect: '对方断线',
  no_capture: '长时间无吃子，判和',
};

export function reasonText(reason: string | null | undefined): string {
  return reason ? LZQ_REASON[reason] ?? reason : '';
}

/** 一条战报的文案。身份缺失（暗棋·不公开）时只说「谁出局」。 */
export function battleText(b: LzqBattle, yourColor: string): string {
  const mine = b.attacker === yourColor ? '我方' : '对方';
  const theirs = b.attacker === yourColor ? '对方' : '我方';
  const aName = b.attacker_piece ? pieceName(b.attacker_piece) : '棋子';
  const dName = b.defender_piece ? pieceName(b.defender_piece) : '棋子';
  if (b.flag_captured) return `${mine}夺旗，${theirs}军旗被夺`;
  if (b.outcome === 'attacker_dies') return `${mine}${aName} 阵亡`;
  if (b.outcome === 'defender_dies') return `${theirs}${dName} 阵亡`;
  return `同归于尽（${mine}${aName} / ${theirs}${dName}）`;
}
