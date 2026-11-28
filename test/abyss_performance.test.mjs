import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const ts = require('typescript');
function load(name) {
  const exports = {};
  const source = readFileSync(new URL(`../app/src/${name}.ts`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  runInNewContext(code, { exports, require: path => path });
  return exports;
}
const { applyAbyssDelta } = load('abyssStream');
const { budgetEffects, projectilePath } = load('abyssRendering');
const { fishSpecies, fishSprites } = load('fishArt');
const plain = value => JSON.parse(JSON.stringify(value));

test('delta updates preserve unchanged references and do not mutate the prior interpolation frame', () => {
  const previous = { frame: 20, players: [{ id: 1 }], enemies: [[1, 'eel', 1, 2], [2, 'crab', 3, 4]], effects: [] };
  const next = applyAbyssDelta(previous, { base: 20, game: { frame: 23 }, rows: {
    enemies: { remove: [1], upsert: [[2, 'crab', 10, 20], [3, 'eel', -10, -20]] },
    effects: { remove: [], upsert: [{ id: 9, kind: 'impact' }] },
  } });
  assert.equal(next.frame, 23);
  assert.equal(next.players, previous.players);
  assert.deepEqual(plain(next.enemies), [[2, 'crab', 10, 20], [3, 'eel', -10, -20]]);
  assert.deepEqual(previous.enemies, [[1, 'eel', 1, 2], [2, 'crab', 3, 4]]);
  assert.equal(applyAbyssDelta(next, { base: 20, game: {}, rows: {} }), null);
  assert.equal(applyAbyssDelta(null, { base: 20, game: {}, rows: {} }), null);
});

test('particle budget always retains terminal projectile contacts, rescue, chest and warning cues', () => {
  const entries = Array.from({ length: 500 }, (_, id) => ({ effect: { id, kind: 'impact' } }));
  entries.push(...['rescue', 'chest', 'nova', 'whip'].map((kind, id) => ({ effect: { id: 1000 + id, kind } })));
  entries.push({ effect: { id: 2000, kind: 'impact', shot: 42 } });
  const result = budgetEffects(entries);
  assert.equal(result.length, 133);
  for (const id of [1000, 1001, 1002, 1003, 2000]) assert.ok(result.some(({ effect }) => effect.id === id));
});

test('batched projectile vertices follow the actual projectile heading', () => {
  assert.ok(projectilePath(20, 40, 0, false).startsWith('M32.0 40.0'));
  assert.ok(projectilePath(20, 40, Math.PI / 2, false).startsWith('M20.0 52.0'));
  assert.ok(projectilePath(20, 40, Math.PI, true).startsWith('M3.0 40.0'));
});

test('all twelve fish occur across size bands with stable species for an unchanged entity', () => {
  const species = new Set();
  for (const r of [7, 16, 23, 31, 41, 56, 73, 92]) {
    for (const id of [20, 21]) {
      species.add(fishSpecies(r, id));
      assert.equal(fishSpecies(r, id), fishSpecies(r, id));
    }
  }
  assert.equal(species.size, 12);
  assert.deepEqual([...species].sort(), Object.keys(fishSprites).sort());
  assert.equal(fishSpecies(105, -1), 'shark');
});
