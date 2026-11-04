// Execute shared production chrome in screen harnesses with the same platform adapters.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

export function installGameChrome(modules, ts) {
  modules['./theme'] = modules['../theme'];
  modules['./backGuard'] = modules['../backGuard'];
  modules['./help'] = modules['../help'];
  modules['./games'] = { gameTitle: key => key };
  const load = name => {
    const exports = {};
    const code = ts.transpileModule(readFileSync(new URL(`../app/src/${name}`, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
    }).outputText;
    runInNewContext(code, { exports, require: name => {
      assert.ok(name in modules, `Unexpected chrome dependency: ${name}`);
      return modules[name];
    } });
    return exports;
  };
  modules['./ui'] = load('ui.tsx');
  modules['./GameClock'] = modules['../GameClock'] = load('GameClock.tsx');
  modules['../GameShell'] = load('GameShell.tsx');
  modules['../gamePlayers'] = load('gamePlayers.ts');
}
