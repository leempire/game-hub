export type FishDirection = readonly [number, number];
export type FishItem = 'boost' | 'shield';
export type FishRank = { id: number; name: string; score: number; rank: number | null; lives: number };
export type FishPlayer = {
  id: number; name: string; x: number; y: number; r: number; facing: number;
  lives: number; score: number; immune: number; boost: boolean; shield: boolean;
  tier?: number; growth_from?: number; growth_to?: number | null; speed?: number;
};
export type FishState = {
  game: 'fish'; frame: number; width: number; height: number; remaining: number;
  event: FishEvent | null; finished: boolean; players: FishPlayer[];
  duration?: number; event_remaining?: number; upcoming?: [FishEvent, number] | null;
  fish: [id: number, x: number, y: number, r: number, facing: number][];
  items: [id: number, x: number, y: number, kind: FishItem][];
  ranking: FishRank[];
};

export type FishEvent = 'school' | 'current';
export const fishEventName = { school: '鱼群迁徙', current: '急流来袭' };
export const still: FishDirection = [0, 0];
export const fishEventText = { school: '鱼群迁徙 · 小鱼增多', current: '急流 · 野生鱼加速' };

export function swimDirection(x: number, y: number, fullSpeedAt = 1): FishDirection {
  const scale = Math.max(fullSpeedAt, Math.hypot(x, y));
  return [x / scale, y / scale];
}
