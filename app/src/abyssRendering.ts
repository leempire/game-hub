import type { AbyssState } from './abyss';

// Pure drawing geometry: never used for collision, damage or pickup decisions.
export const batchableShots = new Set(['rivet', 'holy_wand', 'harpoon', 'thousand_edge']);
export const projectileBatchThreshold = 80;
export const decorativeEffectLimit = 128;

export function projectilePath(x: number, y: number, angle: number, heavy: boolean) {
  const cos = Math.cos(angle), sin = Math.sin(angle);
  const point = (dx: number, dy: number) =>
    `${(x + dx * cos - dy * sin).toFixed(1)} ${(y + dx * sin + dy * cos).toFixed(1)}`;
  const length = heavy ? 17 : 12;
  return `M${point(length, 0)}L${point(-9, -3)}L${point(-6, 0)}L${point(-9, 3)}Z`;
}

export function budgetEffects<T extends { effect: AbyssState['effects'][number] }>(entries: T[], focus?: readonly number[]) {
  const cosmetic = ({ effect }: T) => effect.shot === undefined
    && !(effect.kind === 'defeat' && (effect.r ?? 0) >= 28)
    && ['impact', 'burst', 'defeat'].includes(effect.kind ?? '');
  const candidates = entries.filter(cosmetic);
  if (candidates.length <= decorativeEffectLimit) return entries;
  if (focus) candidates.sort((a, b) => {
    const distance = ({ effect }: T) => ((effect.x ?? 0) - focus[0]) ** 2 + ((effect.y ?? 0) - focus[1]) ** 2;
    return distance(a) - distance(b);
  });
  const selected = new Set(candidates.slice(0, decorativeEffectLimit));
  // Terminal contacts and gameplay cues stay intact.
  return entries.filter(entry => !cosmetic(entry) || selected.has(entry));
}

export function impactPath(x: number, y: number, angle: number, scale: number) {
  const cos = Math.cos(angle) * scale, sin = Math.sin(angle) * scale;
  const point = (dx: number, dy: number) =>
    `${(x + dx * cos - dy * sin).toFixed(1)} ${(y + dx * sin + dy * cos).toFixed(1)}`;
  return [[-12, 0, -3, 0], [3, 0, 17, 0], [2, -3, 11, -10],
    [2, 3, 11, 10], [-2, -3, -6, -8], [-2, 3, -6, 8]]
    .map(([x0, y0, x1, y1]) => `M${point(x0, y0)}L${point(x1, y1)}`).join('');
}
