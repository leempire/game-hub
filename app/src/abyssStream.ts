import type { AbyssState } from './abyss';

const rowFields = ['enemies', 'loot', 'chests', 'caches', 'effects', 'fields', 'shots'] as const;
type RowField = typeof rowFields[number];
type Row = { id: number } | readonly [number, ...unknown[]];
export interface AbyssDelta {
  base: number;
  game: Partial<AbyssState>;
  rows: Partial<Record<RowField, { upsert: Row[]; remove: number[] }>>;
}

export function applyAbyssDelta(previous: AbyssState | null, delta: AbyssDelta): AbyssState | null {
  if (!previous || previous.frame !== delta.base) return null;
  const game = { ...previous, ...delta.game };
  for (const key of rowFields) {
    const patch = delta.rows[key];
    if (!patch) continue;
    const id = (row: Row) => 'id' in row ? row.id : row[0];
    const rows = new Map((previous[key] as Row[] | undefined ?? []).map(row => [id(row), row]));
    for (const uid of patch.remove) rows.delete(uid);
    for (const row of patch.upsert) rows.set(id(row), row);
    // Each negotiated row field keeps its existing tuple/object schema.
    Object.assign(game, { [key]: [...rows.values()] });
  }
  return game;
}
