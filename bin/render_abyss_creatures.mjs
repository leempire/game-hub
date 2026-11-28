#!/usr/bin/env node
// Bake existing vector creatures into sprites: node bin/render_abyss_creatures.mjs
// Then run: python3 bin/render_abyss_creatures.py
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
const require = createRequire(new URL('../app/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');
function load(name, dependencies = {}) {
  const exports = {};
  const source = readFileSync(new URL(`../app/src/${name}`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true,
  } }).outputText;
  runInNewContext(code, { exports, require: name => {
    if (name.endsWith('.webp')) return name;
    if (name.endsWith('.json')) return {};
    if (!(name in dependencies)) throw new Error(`Unexpected import: ${name}`);
    return dependencies[name];
  } });
  return exports;
}
const theme = load('theme.ts');
const svg = { __esModule: true, default: 'svg', Circle: 'circle', Defs: 'defs', Ellipse: 'ellipse', G: 'g', Path: 'path' };
const { CreatureDefinitions } = load('AbyssCreatures.tsx', {
  react: React, 'react-native': { StyleSheet: { create: x => x } }, 'react-native-svg': svg, './theme': theme,
});
const definitions = renderToStaticMarkup(React.createElement(CreatureDefinitions, { prefix: 'bake', raster: false }));
const directory = new URL('../app/assets/abyss/creatures/', import.meta.url);
mkdirSync(directory, { recursive: true });
for (const kind of ['drifter', 'eel', 'crab', 'needlefish', 'jelly', 'angler', 'elite']) {
  const source = `<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="-40 -40 80 80">${definitions}<use href="#bake-creature-${kind}"/></svg>`;
  writeFileSync(new URL(`${kind}.svg`, directory), source);
}
