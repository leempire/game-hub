/**
 * 棋类终局快照的**逐步回放**（只做客户端重建，不新增接口）。
 *
 * 服务端在 v1.18.1 起把 `moves` 写进快照（五子棋 / 翻转棋 / 点格棋 / 四子棋；
 * 象棋与军棋本来就有）。这里用「空盘 + 前 N 手」重建第 N 手的局面，
 * 渲染仍复用 HistoryScreen 里现成的棋盘组件。
 *
 * ⚠️ 只覆盖**规则简单到能安全重放**的四种棋；象棋/军棋别在这里重写走法
 * （客户端不实现棋规是同一条教训）。
 */

export const REPLAYABLE_GAMES = new Set(['gomoku', 'reversi', 'dots', 'connect4']);

export function replayStepCount(snap: any): number {
  if (!snap || !REPLAYABLE_GAMES.has(snap.game)) return 0;
  return Array.isArray(snap.moves) ? snap.moves.length : 0;
}

function emptyBoard(rows: number, cols: number): number[][] {
  return Array.from({ length: rows }, () => Array(cols).fill(0));
}

/** 第 `step` 手结束后的完整快照（step=0 是开局）。 */
export function snapshotAtStep(snap: any, step: number): any {
  const moves = Array.isArray(snap?.moves) ? snap.moves : [];
  const n = Math.max(0, Math.min(step, moves.length));
  const atEnd = n >= moves.length;
  const finish = (view: any) => ({
    ...view,
    winner: atEnd ? view.winner : null,
    reason: atEnd ? view.reason : null,
  });

  if (snap?.game === 'gomoku') {
    const size = snap.board_size || 15;
    const board = emptyBoard(size, size);
    for (const m of moves.slice(0, n)) {
      const stone = m.color === 'black' ? 1 : 2;
      if (board[m.y]?.[m.x] !== undefined) board[m.y][m.x] = stone;
    }
    return finish({ ...snap, board });
  }

  if (snap?.game === 'reversi') {
    const size = snap.board_size || 8;
    const board = emptyBoard(size, size);
    // 初始四子（与 server/app/game/reversi.py 的 INITIAL_STONES 一致）
    if (size === 8) {
      board[3][3] = 2; board[4][4] = 2;
      board[4][3] = 1; board[3][4] = 1;
    }
    for (const m of moves.slice(0, n)) {
      const stone = m.color === 'black' ? 1 : 2;
      if (board[m.y]?.[m.x] === undefined) continue;
      board[m.y][m.x] = stone;
      for (const [fx, fy] of (m.flipped || [])) {
        if (board[fy]?.[fx] !== undefined) board[fy][fx] = stone;
      }
    }
    return finish({ ...snap, board });
  }

  if (snap?.game === 'connect4') {
    const rows = snap.rows || 6;
    const cols = snap.cols || 7;
    const board = emptyBoard(rows, cols);
    for (const m of moves.slice(0, n)) {
      if (board[m.y]?.[m.x] !== undefined) {
        board[m.y][m.x] = m.color === 'black' ? 1 : 2;
      }
    }
    return finish({ ...snap, board });
  }

  if (snap?.game === 'dots') {
    const dots = snap.dots || 5;
    const rows = dots - 1;
    const cols = dots - 1;
    const h = Array.from({ length: rows + 1 }, () => Array(cols).fill(0));
    const v = Array.from({ length: rows }, () => Array(cols + 1).fill(0));
    const boxes = emptyBoard(rows, cols);
    const scores: Record<number, number> = { 1: 0, 2: 0 };
    for (const m of moves.slice(0, n)) {
      const player = m.player === 2 ? 2 : 1;
      if (m.orient === 'h') {
        if (h[m.y]?.[m.x] !== undefined) h[m.y][m.x] = player;
      } else if (v[m.y]?.[m.x] !== undefined) {
        v[m.y][m.x] = player;
      }
      for (const [by, bx] of (m.completed || [])) {
        if (boxes[by]?.[bx] !== undefined) {
          boxes[by][bx] = player;
          scores[player] += 1;
        }
      }
    }
    return finish({ ...snap, h, v, boxes, scores });
  }

  return snap;
}
