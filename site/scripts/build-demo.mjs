#!/usr/bin/env node
/**
 * Builds the interactive demo and copies it into site/public/demo/, so the
 * site publishes it at /agents_hub/demo/.
 *
 * The demo is the real dashboard (dashboard/frontend) built with VITE_DEMO=1:
 * a mock service worker answers every /api call from recorded fixtures
 * (dashboard/frontend/src/demo/), so it runs on GitHub Pages with no backend.
 *
 * A docs only checkout, one where the frontend's dependencies were never
 * installed, skips the demo with a message instead of failing the build: the
 * site still builds, just without /demo/.
 *
 *   node scripts/build-demo.mjs            build and copy
 *   DEMO_SKIP=1 node scripts/build-demo.mjs   skip on purpose
 */
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SITE = join(HERE, '..');
const FRONTEND = join(SITE, '..', 'dashboard', 'frontend');
const OUT = join(FRONTEND, 'dist-demo');
const DEST = join(SITE, 'public', 'demo');

if (process.env.DEMO_SKIP === '1') {
  console.log('[demo] DEMO_SKIP=1, not building the demo.');
  process.exit(0);
}

if (!existsSync(join(FRONTEND, 'node_modules'))) {
  console.log(
    '[demo] dashboard/frontend/node_modules is missing, skipping the demo build.\n'
    + '[demo] Run `npm ci` in dashboard/frontend first to include /demo/ in the site.',
  );
  process.exit(0);
}

console.log('[demo] building dashboard/frontend with VITE_DEMO=1');
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const result = spawnSync(npm, ['run', 'build:demo'], {
  cwd: FRONTEND,
  stdio: 'inherit',
  env: { ...process.env, VITE_DEMO: '1' },
});
if (result.status !== 0) {
  console.error(`[demo] build:demo failed with exit code ${result.status}`);
  process.exit(result.status || 1);
}
if (!existsSync(join(OUT, 'index.html'))) {
  console.error(`[demo] expected ${OUT}/index.html after the build, found nothing`);
  process.exit(1);
}

rmSync(DEST, { recursive: true, force: true });
mkdirSync(DEST, { recursive: true });
cpSync(OUT, DEST, { recursive: true });
console.log(`[demo] copied ${OUT} -> ${DEST}`);
