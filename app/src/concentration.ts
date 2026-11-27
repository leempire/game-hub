export type ConcentrationSuit = 'S' | 'H' | 'C' | 'D';

export type ConcentrationSlot = { state: 'hidden' | 'collected' }
  | { state: 'revealed'; rank: string; suit: ConcentrationSuit };

export interface ConcentrationScore {
  id: number;
  pairs: number;
  rank?: number | null;
  forfeited?: boolean;
}

export interface ConcentrationView {
  game: 'concentration';
  match_id: string;
  revision: number;
  slots: ConcentrationSlot[];
  turn_id: number | null;
  remaining_pairs: number;
  phase: 'first' | 'second' | 'revealing' | 'finished';
  outcome: 'match' | 'miss' | null;
  scores: ConcentrationScore[];
  reason?: 'completed' | 'surrender' | 'leave' | 'disconnect';
}

export interface ConcentrationSnapshot {
  game: 'concentration';
  scores: ConcentrationScore[];
  card_count: number;
}

export const CONCENTRATION_COLUMNS = 4;

export const concentrationSuits = {
  S: { symbol: '♠', name: '黑桃', red: false },
  H: { symbol: '♥', name: '红桃', red: true },
  C: { symbol: '♣', name: '梅花', red: false },
  D: { symbol: '♦', name: '方块', red: true },
};

export function concentrationPosition(index: number): string {
  return `${Math.floor(index / CONCENTRATION_COLUMNS) + 1} 行 ${index % CONCENTRATION_COLUMNS + 1} 列`;
}

export function concentrationFocus(index: number, key: string, count: number): number | undefined {
  const column = index % CONCENTRATION_COLUMNS;
  switch (key) {
    case 'ArrowLeft': return column > 0 ? index - 1 : index;
    case 'ArrowRight': return column < CONCENTRATION_COLUMNS - 1 && index + 1 < count ? index + 1 : index;
    case 'ArrowUp': return index >= CONCENTRATION_COLUMNS ? index - CONCENTRATION_COLUMNS : index;
    case 'ArrowDown': return index + CONCENTRATION_COLUMNS < count ? index + CONCENTRATION_COLUMNS : index;
    default: return undefined;
  }
}
