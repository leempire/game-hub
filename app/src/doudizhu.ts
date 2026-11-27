import type { PokerPlayRecord } from './PokerPlayHistory';

export interface DoudizhuRules {
  all_pass: 'redeal' | 'first_bidder';
  airplane_pair_as_singles: boolean;
  four_pair_as_singles: boolean;
}

interface DoudizhuPlay {
  player_id: number;
  cards: string[];
  kind: string;
  label: string;
}

type DoudizhuSeatAction = { kind: 'bid'; bid: number } | { kind: 'play' | 'pass' };

export interface DoudizhuView {
  game: 'doudizhu';
  match_id: string;
  revision: number;
  phase: 'bidding' | 'playing' | 'finished';
  seat_uids: number[];
  current_id: number | null;
  landlord_id: number | null;
  hand: string[];
  remaining: number[];
  bottom: string[];
  bid: number;
  multiplier: number;
  available_bids: number[];
  can_pass: boolean;
  last_play: DoudizhuPlay | null;
  play_history?: PokerPlayRecord<DoudizhuPlay>[];
  actions: (DoudizhuSeatAction | null)[];
  rules: DoudizhuRules;
}

export interface DoudizhuSnapshot {
  match_id: string;
  revision: number;
  seat_uids: number[];
  landlord_id: number | null;
  winner: 'landlord' | 'farmers' | null;
  reason: 'empty_hand' | 'surrender' | 'leave' | 'disconnect' | 'canceled';
  bid: number;
  multiplier: number;
  bombs: number;
  rockets: number;
  spring: 'spring' | 'reverse' | null;
  scores: Record<string, number> | null;
  last_play: DoudizhuPlay | null;
  play_history?: PokerPlayRecord<DoudizhuPlay>[];
  rules: DoudizhuRules;
  remaining: number[];
}

export interface DoudizhuResultMessage {
  type: 'game_over';
  game: 'doudizhu';
  match_id: string;
  summary: DoudizhuSnapshot;
}

export type DoudizhuAction = { action: 'bid'; bid: number } | { action: 'play'; cards: string[] }
  | { action: 'pass' | 'hint' };

export function doudizhuRole(uid: number, landlordId: number | null) {
  if (landlordId === null) return '身份待定';
  return uid === landlordId ? '地主' : '农民';
}

const reasonLabels = {
  empty_hand: '出完手牌', surrender: '玩家认输', leave: '玩家离开房间', disconnect: '玩家离线超时', canceled: '叫分阶段取消',
};

export function formatDoudizhuResult(result: DoudizhuSnapshot) {
  const headline = result.winner === 'landlord' ? '地主胜利' : result.winner === 'farmers' ? '农民胜利' : '本局取消';
  const detail = [reasonLabels[result.reason]];
  if (result.bid > 0 && result.landlord_id !== null) {
    detail.push(`底分 ${result.bid} · 倍数 ×${result.multiplier}`);
    if (result.bombs || result.rockets || result.spring) {
      const sources = [`炸弹 ${result.bombs}`, `王炸 ${result.rockets}`];
      if (result.spring) sources.push(result.spring === 'spring' ? '春天' : '反春');
      detail.push(sources.join(' · '));
    }
  }
  return { headline, detail: detail.join('\n') };
}

const suits: Record<string, { glyph: string; name: string; red: boolean }> = {
  S: { glyph: '♠', name: '黑桃', red: false },
  H: { glyph: '♥', name: '红桃', red: true },
  C: { glyph: '♣', name: '梅花', red: false },
  D: { glyph: '♦', name: '方片', red: true },
};
const ranks = ['BJ', 'SJ', '2', 'A', 'K', 'Q', 'J', '10', '9', '8', '7', '6', '5', '4', '3'];

function rankOf(card: string) {
  return card === 'BJ' || card === 'SJ' ? card : card.slice(0, -1);
}

export function sortDoudizhuHand(cards: readonly string[]) {
  const suitOrder = 'SHCD';
  return [...cards].sort((a, b) =>
    ranks.indexOf(rankOf(a)) - ranks.indexOf(rankOf(b)) || suitOrder.indexOf(a.at(-1) ?? '') - suitOrder.indexOf(b.at(-1) ?? ''));
}

export function describeDoudizhuCard(card: string) {
  const rank = rankOf(card);
  if (rank === 'BJ' || rank === 'SJ') {
    const joker = rank === 'BJ' ? '大王' : '小王';
    return { rank: joker, suit: '', label: joker, red: rank === 'BJ' };
  }
  const suit = suits[card.slice(-1)];
  return { rank, suit: suit?.glyph ?? '', label: `${suit?.name ?? ''}${rank}`, red: suit?.red ?? false };
}

export function describeDoudizhuPlay(play: DoudizhuPlay) {
  return { label: play.label, cards: play.cards.map(describeDoudizhuCard) };
}

export const doudizhuCardWidth = 52;
const handStepMinimum = 24;
const handWidthForTwenty = doudizhuCardWidth + (20 - 1) * handStepMinimum;

export function layoutDoudizhuHand(hand: string[], width: number) {
  const perRow = width >= handWidthForTwenty
    ? 20 : Math.max(1, Math.min(10, Math.floor((width - doudizhuCardWidth) / handStepMinimum) + 1));
  const sorted = sortDoudizhuHand(hand);
  // Use the same step across both rows so after cards are played the columns stay aligned.
  const columns = Math.min(sorted.length, perRow);
  const step = columns < 2 ? doudizhuCardWidth : Math.min(46, Math.floor((width - doudizhuCardWidth) / (columns - 1)));
  const rows: string[][] = [];
  for (let start = 0; start < sorted.length; start += perRow) rows.push(sorted.slice(start, start + perRow));
  return { rows, step };
}
