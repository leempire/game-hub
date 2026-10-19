/**
 * Minesweeper 的**协议类型与展示常量**（颜色等），不含任何档位表。
 *
 * ⚠️ 这里原本还有一张客户端档位表（`MINE_DIFFICULTIES` / `MINE_DIFFICULTY_KEYS` /
 * `mineDifficultyBlurb`），v1.15 删掉了：难度名与说明现在随房间级设置下发
 * （`room.settings` 的 `difficulty` 项，见 `server/app/room_settings.py`），
 * 棋盘几何也一直来自服务端的 `MineGameState`。那张表**没有任何渲染路径在读它**
 * —— 四个使用点全都只是取 `label`。留着它就是留一份会和服务端漂移的副本。
 */

// Cell value: -1 = mine, 0 = empty, 1-8 = adjacent mine count
export type CellValue = number;

// Revealed cell from server: [x, y, value]
export type RevealedCell = [number, number, number];

// Flagged cell from server: [x, y]
export type FlaggedCell = [number, number];

/** Per-player state inside game.players[seat] */
export interface MinePlayerState {
  alive: boolean;
  /** 剩余命数；0 表示已淘汰。服务端 game_to_sync 逐人下发。 */
  lives_left: number;
  revealed_count: number;
}

/** Ranking entry received in game_over and sync (when finished) */
export interface MineRankingEntry {
  seat: number;
  username: string;
  nickname: string | null;
  alive: boolean;
  correct_flags: number;
  wrong_flags: number;
  revealed_count: number;
  rank: number;
}

/** Game state received via sync / game_start messages */
export interface MineGameState {
  game: 'minesweeper';
  width: number;
  height: number;
  mines: number;
  /** 本局每人几条命（按难度定：1/2/3）。 */
  lives: number;
  revealed: RevealedCell[];
  flagged: FlaggedCell[];
  started: boolean;
  finished: boolean;
  reason: 'cleared' | 'eliminated' | null;
  players: Record<string, MinePlayerState>;
  ranking?: MineRankingEntry[];
}

/** Incoming WS message: revealed cells */
export interface MineRevealedMsg {
  type: 'revealed';
  seat: number;
  username: string;
  cells: RevealedCell[];
  hit_mine: boolean;
  /** 这次操作是否扣掉了一条命（命中雷）。 */
  lost_life: boolean;
  /** 操作者扣命后的剩余命数。 */
  lives_left: number;
  eliminated: boolean;
  revealed_count: number;
}

/** Incoming WS message: 踩雷但命没扣完（掉一条命，未淘汰） */
export interface MineLifeLostMsg {
  type: 'life_lost';
  seat: number;
  username: string;
  lives_left: number;
  message: string;
}

/** Incoming WS message: flag toggled */
export interface MineFlaggedMsg {
  type: 'flagged';
  x: number;
  y: number;
  flagged: boolean;
  seat: number;
}

/** Incoming WS message: player eliminated */
export interface MinePlayerEliminatedMsg {
  type: 'player_eliminated';
  seat: number;
  username: string;
  message: string;
}

/** Incoming WS message: game over */
export interface MineGameOverMsg {
  type: 'game_over';
  reason: 'cleared' | 'eliminated';
  ranking: MineRankingEntry[];
}

// 数字配色（沿用扫雷传统色相，但每档都加深到在翻开格 #EDEFF2 上 ≥4.8:1）。
// 经典配色里 5 号的琥珀 #FF8F00 只有 1.99:1——那不是色觉问题，是所有人都看不清。
export const NUMBER_COLORS: Record<number, string> = {
  1: '#1565C0',  // blue    4.99
  2: '#1B5E20',  // green   6.83
  3: '#C62828',  // red     4.88
  4: '#6A1B9A',  // purple  8.15
  5: '#BF360C',  // deep orange 4.86
  6: '#00695C',  // teal    5.74
  7: '#37474F',  // dark gray 8.38
  8: '#455A64',  // gray    6.29
};
