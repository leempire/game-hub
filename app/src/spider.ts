/** 服务端权威视图；客户端不推演蜘蛛纸牌规则。 */
export interface SpiderMove { source: number; index: number; target: number; }
export interface SpiderState {
  columns: (number | null)[][];
  stock_count: number;
  completed: number;
  total_runs: number;
  move_count: number;
  revision: number;
  can_undo: boolean;
  can_deal: boolean;
  legal_moves: SpiderMove[];
  finished: boolean;
  winner: string | null;
  reason: string | null;
}
export interface SpiderSnapshot extends SpiderState { game: 'spider'; }

export interface SpiderProgress {
  completed: number;
  total_runs: number;
  reason: 'completed' | 'abandoned' | null;
}

export function spiderProgressLabel({ completed, total_runs, reason }: SpiderProgress): string {
  const count = `${completed}/${total_runs}`;
  if (reason === 'completed') return `通关 ${count}`;
  if (reason === 'abandoned') return `结束 ${count}`;
  return `${count} 组`;
}

export const spiderRank = (rank: number) => ({ 1: 'A', 11: 'J', 12: 'Q', 13: 'K' }[rank] ?? String(rank));
