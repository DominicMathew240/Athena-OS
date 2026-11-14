import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const app = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
assert.match(app, /GHSA-gv7w-rqvm-qjhr/);
assert.match(app, /was withdrawn/);
assert.match(app, /esbuild.*Deno/);
assert.match(app, /https:\/\/github\.com\/advisories\/GHSA-gv7w-rqvm-qjhr/);
console.log('Security advisory notice present');
