// 中国象棋棋子字符与棋盘尺寸常量（**单一来源**：对局屏与战绩回放共用）。
//
// 引擎（server/app/xiangqi.py）用单字符表示棋子，红方大写、黑方小写，与象棋
// FEN 一致。这里只把字符映射成界面上的汉字，**不含任何走法规则**——规则只在
// 服务端有一份，客户端拿服务端下发的 `legal` 列表做高亮。
// （飞行棋踩过「客户端自己推路径画错」的坑，同一条教训。）

export const XIANGQI_ROWS = 10;
export const XIANGQI_COLS = 9;

/** 棋盘坐标系：行 0 在上（黑方底线），行 9 在下（红方底线）；红先。 */
export const XIANGQI_RED = 'red';
export const XIANGQI_BLACK = 'black';

/** 红方棋子是大写字符。 */
export function isRedPiece(piece: string): boolean {
  return piece === piece.toUpperCase();
}

/** 棋子字符 → 界面汉字。红黑两套叫法不同：帅/将、仕/士、相/象、兵/卒。 */
const GLYPHS: Record<string, string> = {
  K: '帅', A: '仕', B: '相', N: '马', R: '车', C: '炮', P: '兵',
  k: '将', a: '士', b: '象', n: '马', r: '车', c: '炮', p: '卒',
};

export function pieceGlyph(piece: string | null): string {
  return piece ? GLYPHS[piece] || '' : '';
}

/** 棋子字符 → 读屏用的中文名（无障碍标签用，颜色不能是唯一通道）。 */
export function pieceName(piece: string | null): string {
  if (!piece) return '空位';
  const glyph = GLYPHS[piece];
  return glyph ? `${isRedPiece(piece) ? '红' : '黑'}${glyph}` : '棋子';
}

/** 红黑双方的中文名（对局屏与回放共用，别再各写一份）。 */
export const COLOR_LABELS: Record<string, string> = {
  [XIANGQI_RED]: '红方',
  [XIANGQI_BLACK]: '黑方',
};

/**
 * 棋盘翻转：黑方视角要把棋盘转 180°，让自己这边在下方。
 *
 * 这个变换是**对合**（做两次回原样），所以同一个函数既能做「逻辑 → 显示」
 * 也能做「显示 → 逻辑」——点棋盘时用它把点击位置换回引擎坐标。
 */
export function flipSquare(
  r: number, c: number, flipped: boolean,
): [number, number] {
  return flipped
    ? [XIANGQI_ROWS - 1 - r, XIANGQI_COLS - 1 - c]
    : [r, c];
}
