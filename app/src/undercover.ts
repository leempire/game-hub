export type UndercoverRole = 'civilian' | 'undercover';
type UndercoverWinner = UndercoverRole | 'draw' | 'aborted';

interface UndercoverElimination {
  round: number;
  user_id: number;
  role: UndercoverRole;
  reason: string;
}

export interface UndercoverView {
  match_id: string;
  revision: number;
  phase_id: number;
  phase: 'clue' | 'vote' | 'runoff' | 'finished';
  round: number;
  remaining_ms: number;
  paused?: boolean;
  speaker_id: number | null;
  player_ids: number[];
  alive: number[];
  turn_order: number[];
  clues: { round: number; user_id: number; text: string | null }[];
  eliminations: UndercoverElimination[];
  tallies: { round: number; runoff: boolean; votes: { user_id: number; count: number }[] }[];
  candidate_ids: number[];
  votes_cast: number;
  your_vote: number | null;
  your_word: string | null;
  can_clue: boolean;
}

export interface UndercoverSnapshot {
  game: 'undercover';
  round: number;
  winner: UndercoverWinner;
  reason: string;
  roles?: { user_id: number; role: UndercoverRole }[];
  word_pair?: Record<UndercoverRole, string> | null;
  eliminations?: UndercoverElimination[];
  tallies?: UndercoverView['tallies'];
}

export interface UndercoverResultMessage {
  game: 'undercover';
  match_id: string;
  winner: UndercoverWinner;
  reason: string;
  summary: UndercoverSnapshot;
  forfeited: number[];
}

export const undercoverRoleLabels: Record<UndercoverRole, string> = { civilian: '平民', undercover: '卧底' };
export const undercoverWinnerLabels: Record<UndercoverWinner, string> = {
  civilian: '平民胜利', undercover: '卧底胜利', draw: '和局', aborted: '对局中止',
};
export const undercoverReasonLabels: Partial<Record<string, string>> = {
  no_undercover: '卧底已全部出局',
  parity: '存活卧底人数已追平或超过平民',
  round_limit: '第 8 轮结束，双方仍未分出胜负',
  abandoned: '所有玩家已离场',
};
