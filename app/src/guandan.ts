export interface GuandanCard {
  id: number;
  rank: string;
  suit: string;
}

const comboLabels = {
  single: '单张', pair: '对子', triple: '三张', full_house: '三带二', straight: '顺子', tube: '三连对',
  plate: '钢板', bomb: '炸弹', straight_flush: '同花顺', joker_bomb: '四王炸',
};

export interface GuandanCombo {
  kind: keyof typeof comboLabels;
  rank: string;
  cards: GuandanCard[];
  wild: GuandanCard[];
}

export interface GuandanDealResult {
  deal: number;
  winner: 0 | 1;
  finish_order: number[];
  levels: [string, string];
  advance: number;
  level: string;
}

export interface GuandanVersion {
  match_id: string;
  revision: number;
  deal: number;
}

export interface GuandanView extends GuandanVersion {
  game: 'guandan';
  phase: 'returning' | 'playing' | 'between' | 'finished';
  players: number[];
  levels: [string, string];
  level: string;
  level_team: 0 | 1 | null;
  current_id: number | null;
  hand: GuandanCard[];
  remaining: (number | null)[];
  passed: number[];
  finish_order: number[];
  last_play: (GuandanCombo & { uid: number }) | null;
  exchange: {
    antitribute: boolean;
    lead_id: number;
    pairs: {
      from_id: number;
      to_id: number;
      tribute: GuandanCard;
      returned: GuandanCard | null;
      submitted: boolean;
    }[];
  } | null;
  return_choices: number[];
  lead_reason: 'first' | 'pass' | 'partner' | 'tribute' | 'antitribute' | null;
  deal_result: GuandanDealResult | null;
}

export interface GuandanSnapshot {
  match_id: string;
  players: number[];
  levels: [string, string];
  deal: number;
  winner: 0 | 1 | null;
  reason: 'clear_ace' | 'surrender' | 'leave' | 'disconnect' | null;
  deal_result: GuandanDealResult | null;
}

export interface GuandanResultMessage {
  type: 'game_over';
  game: 'guandan';
  match_id: string;
  summary: GuandanSnapshot;
}

export type GuandanCommand = 'play' | 'pass' | 'return' | 'next';
export type GuandanQuery = 'hint' | 'preview';

export function guandanTeam(players: number[], uid?: number) {
  const seat = uid === undefined ? -1 : players.indexOf(uid);
  return seat < 0 ? null : seat % 2;
}

export function guandanRanks(players: number[], result: GuandanDealResult) {
  const places = ['头游', '二游', '三游', '末游'];
  return players.map(id => ({ id, place: places[result.finish_order.indexOf(id)] ?? '双下' }));
}

export function formatGuandanResult(result: GuandanSnapshot, viewerId?: number) {
  const team = guandanTeam(result.players, viewerId);
  const winner = result.winner;
  const headline = winner === null ? '对局结束' : team === null ? `第 ${winner + 1} 队胜利`
    : winner === team ? '我方胜利' : '对方胜利';
  const reasons = { clear_ace: '成功过 A', surrender: '有玩家认输', leave: '有玩家离开房间', disconnect: '有玩家离线超时' };
  const score = team === null ? `第 1 队 ${result.levels[0]} · 第 2 队 ${result.levels[1]}`
    : `我方 ${result.levels[team]} · 对方 ${result.levels[1 - team]}`;
  const detail = [result.reason && reasons[result.reason],
    `第 ${result.deal} 副 · ${score}`].filter(Boolean).join('\n');
  return { headline, detail };
}

const suits: Record<string, { glyph: string; name: string }> = {
  S: { glyph: '♠', name: '黑桃' }, H: { glyph: '♥', name: '红桃' },
  D: { glyph: '♦', name: '方片' }, C: { glyph: '♣', name: '梅花' },
};

export function describeGuandanCard(card: GuandanCard) {
  const suit = suits[card.suit];
  const rank = card.rank === 'BJ' ? '大王' : card.rank === 'SJ' ? '小王' : card.rank;
  return { rank, suit: suit?.glyph, label: `${suit?.name ?? ''}${rank}`,
    red: card.suit === 'H' || card.suit === 'D' || card.rank === 'BJ' };
}

export function isGuandanWild(card: GuandanCard, level: string) {
  return card.rank === level && card.suit === 'H';
}

export function describeGuandanCombo(combo: GuandanCombo, level: string) {
  const label = combo.kind === 'bomb' ? `${combo.cards.length} 张炸弹` : comboLabels[combo.kind];
  const rank = combo.rank === 'BJ' ? '大王' : combo.rank === 'SJ' ? '小王' : combo.rank;
  const substitutions = combo.wild.map(card => `红桃${level} 当${describeGuandanCard(card).label}`);
  return [combo.kind === 'joker_bomb' ? label : `${label} ${rank}`, ...substitutions].join(' · ');
}

export const guandanHandInset = 12;
export const guandanTableMax = 1120;
export const guandanActionRail = 104;
export const guandanWideHand = 788;

export function layoutGuandanHand(hand: GuandanCard[], width: number) {
  const wide = width >= guandanWideHand;
  const cardWidth = wide ? 60 : 48;
  const minimumStep = wide ? 28 : 24;
  const capacity = Math.max(1, Math.floor((width - cardWidth) / minimumStep) + 1);
  const rowCount = Math.ceil(hand.length / capacity);
  const columns = Math.ceil(hand.length / Math.max(1, rowCount));
  const step = columns < 2 ? cardWidth : Math.min(cardWidth - 2, Math.floor((width - cardWidth) / (columns - 1)));
  const rows: GuandanCard[][] = [];
  for (let index = 0; index < hand.length; index += columns) rows.push(hand.slice(index, index + columns));
  return { rows, step, cardWidth };
}
