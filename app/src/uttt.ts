export type UtttColor = 'black' | 'white';
export type UtttWinner = UtttColor | 'draw' | null;

export interface UtttMove {
  board: number;
  cell: number;
  color: UtttColor;
}

export interface UtttLegalMove {
  board: number;
  cell: number;
  next_board: number | null;
  winner: UtttWinner;
}

export interface UtttState {
  boards: number[][];
  local_results: number[];
  active_board: number | null;
  next: UtttColor;
  your_color: UtttColor;
  winner: UtttWinner;
  reason: string | null;
  revision: number;
  moves: UtttMove[];
  legal: UtttLegalMove[];
}

export const UTTT_BOARD_NAMES = ['左上', '上方', '右上', '左侧', '中央', '右侧', '左下', '下方', '右下'];
export const utttSymbol = (value: number | UtttColor) => value === 1 || value === 'black' ? 'X' : value === 2 || value === 'white' ? 'O' : '';
