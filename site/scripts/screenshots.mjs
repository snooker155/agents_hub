#!/usr/bin/env node
/**
 * Takes the site's screenshots from the interactive demo, so every picture on
 * the site is the real dashboard over the same recorded data a visitor can
 * click through at /demo/.
 *
 *   node scripts/screenshots.mjs                 all shots
 *   node scripts/screenshots.mjs chat flows      only these ids
 *   node scripts/screenshots.mjs --build         rebuild the demo first
 *   node scripts/screenshots.mjs --all           also the shots marked refresh: false
 *
 * It serves dashboard/frontend/dist-demo with `vite preview` on a free port
 * (building it first when it is missing or --build is given), opens each page
 * in headless Chromium at 2560 x 1440 (a 2K screen, so nothing is cramped), once in the light theme and once in
 * the dark one, and writes:
 *
 *   site/public/screenshots/<id>.png                the six landing page shots
 *   site/public/screenshots/recipes/<id>.png        one per recipe in site/recipes/
 *   site/public/screenshots/dark/<id>.png           the same pages, dark theme
 *   site/public/screenshots/dark/recipes/<id>.png
 *
 * The site shows whichever set matches the reader's theme (the image rule in
 * .vitepress/config.mjs swaps /screenshots/ for /screenshots/dark/), and the
 * README uses the dark set.
 *
 * Needs Playwright's Chromium once: `npx playwright install chromium`.
 */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SITE = join(HERE, '..');
const FRONTEND = join(SITE, '..', 'dashboard', 'frontend');
const DIST = join(FRONTEND, 'dist-demo');
const SHOTS_DIR = join(SITE, 'public', 'screenshots');
const DARK_DIR = join(SHOTS_DIR, 'dark');
/** The dashboard's theme is `localStorage.theme` (ThemeContext.jsx): light, dark or system. */
const SCHEMES = ['light', 'dark'];
const RECIPES_DIR = join(SITE, 'recipes');

const W = 2560;
const H = 1440;
const PAGE = 'main header';

/**
 * The six shots on the landing page and in the README. The captions in
 * site/index.md and README.md describe what each one shows, so a shot is
 * taken on a page of the demo workspace that has recorded content: a
 * conversation, the flow, the views gallery, the scenario's setup page.
 *
 * `refresh: false` on an entry keeps its committed picture; none needs it
 * today, the flag is left for a page whose recording lags its caption.
 */
const LANDING = [
  { id: 'assistant', path: '/assistant', waitFor: '[data-tour="assistant-talk"]' },
  { id: 'agents', path: '/agents', waitFor: 'main .grid' },
  // A recorded conversation of the demo workspace, not the empty composer.
  { id: 'chat', path: '/chat/demo_chat_2', waitFor: 'main textarea' },
  // The caption describes the list view; the board is the default.
  { id: 'tasks', path: '/tasks', waitFor: PAGE, click: 'main header button:has-text("List")' },
  { id: 'flows', path: '/flows/demo_content_pipeline', waitFor: '.react-flow__node' },
  // The Views folder of the Artifacts page (the caption still says gallery).
  { id: 'views', path: '/artifacts?folder=__views__', waitFor: 'main' },
  { id: 'playground', path: '/playground/demo_market', waitFor: 'main' },
];

/**
 * Where each recipe's screenshot is taken. A recipe file with no entry here
 * gets the dashboard, so a new recipe still builds (and says so below).
 */
const RECIPE_PAGES = {
  'basic-task': { path: '/tasks', waitFor: PAGE },
  'orchestrated-delivery': { path: '/orchestrator', waitFor: PAGE },
  approval: { path: '/settings/execution', waitFor: 'main section' },
  projects: { path: '/projects', waitFor: PAGE },
  chat: { path: '/chat', waitFor: 'main textarea' },
  flows: { path: '/flows', waitFor: PAGE },
  docker: { path: '/containers', waitFor: PAGE },
  'local-models': { path: '/settings/local', waitFor: 'main section' },
  thinking: { path: '/agents', waitFor: 'main .grid' },
  palette: { path: '/workspaces/demo', waitFor: PAGE },
  'code-panel': { path: '/artifacts?folder=__views__', waitFor: PAGE },
  doctor: { path: '/health', waitFor: PAGE },
  demo: { path: '/chat', waitFor: 'main textarea' },
  lab: { path: '/playground', waitFor: PAGE },
};
const DEFAULT_PAGE = { path: '/dashboard', waitFor: PAGE };

/** Recipe ids from site/recipes/*.md, or the table's own ids without them. */
function recipeIds() {
  if (!existsSync(RECIPES_DIR)) return Object.keys(RECIPE_PAGES);
  const ids = readdirSync(RECIPES_DIR)
    .filter((f) => f.endsWith('.md') && f !== 'index.md')
    .map((f) => f.replace(/\.md$/, ''));
  return ids.length ? ids : Object.keys(RECIPE_PAGES);
}

const FIXTURES = join(FRONTEND, 'src', 'demo', 'fixtures', 'fixtures.json');

function buildShots() {
  // Without recorded fixtures every page is an empty state: good enough for a
  // recipe placeholder, not for replacing the landing page's real shots.
  const recorded = existsSync(FIXTURES);
  if (!recorded) {
    console.warn('[shots] no src/demo/fixtures/fixtures.json, keeping the six landing page shots as they are');
  }
  const shots = recorded
    ? LANDING.filter((s) => s.refresh !== false || process.argv.includes('--all'))
      .map((s) => ({ ...s, file: join(SHOTS_DIR, `${s.id}.png`), dark: join(DARK_DIR, `${s.id}.png`) }))
    : [];
  for (const id of recipeIds()) {
    const page = RECIPE_PAGES[id];
    if (!page) console.warn(`[shots] no page mapped for recipe "${id}", using ${DEFAULT_PAGE.path}`);
    shots.push({
      id: `recipes/${id}`,
      ...(page || DEFAULT_PAGE),
      file: join(SHOTS_DIR, 'recipes', `${id}.png`),
      dark: join(DARK_DIR, 'recipes', `${id}.png`),
    });
  }
  return shots.map((s) => ({ width: W, height: H, ...s }));
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function waitForServer(url, timeoutMs = 30000) {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`the demo server did not answer at ${url} within ${timeoutMs} ms`);
}

/** The base the demo was built for, read back from its index.html. */
function builtBase() {
  const html = readFileSync(join(DIST, 'index.html'), 'utf8');
  const m = html.match(/src="([^"]*?)assets\//);
  return m ? m[1] : '/';
}

async function main() {
  const args = process.argv.slice(2);
  const rebuild = args.includes('--build');
  const only = args.filter((a) => !a.startsWith('--'));

  let chromium;
  try {
    ({ chromium } = await import('playwright'));
  } catch {
    console.error('[shots] playwright is not installed: run `npm install` in site/ first.');
    process.exit(1);
  }

  if (rebuild || !existsSync(join(DIST, 'index.html'))) {
    console.log('[shots] building the demo (npm run build:demo)');
    const r = spawnSync(process.platform === 'win32' ? 'npm.cmd' : 'npm', ['run', 'build:demo'], {
      cwd: FRONTEND, stdio: 'inherit', env: { ...process.env, VITE_DEMO: '1' },
    });
    if (r.status !== 0) process.exit(r.status || 1);
  }

  const base = builtBase();
  const port = await freePort();
  const origin = `http://127.0.0.1:${port}`;
  // VITE_DEMO=1 so vite.config.js serves the build under the base it was
  // built for; VITE_DEMO_BASE carries a non default one through.
  const server = spawn(
    process.platform === 'win32' ? 'npx.cmd' : 'npx',
    ['vite', 'preview', '--outDir', 'dist-demo', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    { cwd: FRONTEND, stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, VITE_DEMO: '1', VITE_DEMO_BASE: base } },
  );
  const stop = () => { if (!server.killed) server.kill('SIGTERM'); };
  process.on('exit', stop);
  process.on('SIGINT', () => { stop(); process.exit(130); });

  let browser;
  const made = [];
  try {
    await waitForServer(`${origin}${base}`);
    try {
      browser = await chromium.launch();
    } catch (e) {
      console.error(`[shots] could not launch Chromium (${e.message.split('\n')[0]}).`);
      console.error('[shots] install it once with: npx playwright install chromium');
      process.exitCode = 1;
      return;
    }
    const context = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, colorScheme: 'light' });
    // The theme starts as light and is switched per shot in capture(), so a
    // screenshot never depends on the machine's own colour scheme.
    await context.addInitScript(() => {
      try {
        if (!localStorage.getItem('theme')) localStorage.setItem('theme', 'light');
      } catch { /* storage unavailable */ }
    });
    // A first visit would open the onboarding modal over every shot.
    await context.addInitScript(() => {
      try {
        localStorage.setItem('agents_hub_onboarding_seen_v2', '1');
        localStorage.setItem('agents_hub_tour_done_v1', '1');
        localStorage.setItem('selectedWorkspace', 'demo');
        localStorage.setItem('agents_hub_language', 'en');
      } catch { /* storage unavailable */ }
    });
    const page = await context.newPage();

    // Load once so the mock service worker is installed before the real shots.
    await page.goto(`${origin}${base}`, { waitUntil: 'networkidle' });

    const shots = buildShots().filter((s) => !only.length || only.includes(s.id) || only.includes(s.id.replace(/^recipes\//, '')));
    // A page that throws renders the route's error boundary. That is not a
    // picture of the product: a landing shot keeps its previous file, a
    // recipe shot falls back to the dashboard so the site still has an image.
    let pageFailed = false;
    page.on('pageerror', () => { pageFailed = true; });
    const capture = async (shot, path, scheme = 'light') => {
      pageFailed = false;
      await page.setViewportSize({ width: shot.width, height: shot.height });
      // The dashboard reads its theme from localStorage when it mounts, and
      // resolves the palette's dark or light ramp from it, so the switch has
      // to happen before the navigation rather than by toggling a class.
      await page.emulateMedia({ colorScheme: scheme });
      await page.evaluate((s) => { try { localStorage.setItem('theme', s); } catch { /* storage unavailable */ } }, scheme);
      await page.goto(`${origin}${base}${path.replace(/^\//, '')}`, { waitUntil: 'networkidle' });
      try {
        await page.waitForSelector(shot.waitFor, { timeout: 8000 });
      } catch {
        console.warn(`[shots] ${shot.id}: "${shot.waitFor}" did not appear on ${path}`);
      }
      if (shot.click) {
        await page.click(shot.click, { timeout: 3000 }).catch(() => console.warn(`[shots] ${shot.id}: could not click ${shot.click}`));
        await page.waitForTimeout(400);
      }
      // The demo pill is for visitors, not for pictures of the product; the
      // rest is a beat for charts and fonts to settle.
      await page.addStyleTag({ content: '[data-demo-badge]{display:none!important}' });
      await page.waitForTimeout(800);
      const broken = pageFailed || (await page.locator('text=This page stopped working').count()) > 0;
      return !broken;
    };

    for (const shot of shots) {
      for (const scheme of SCHEMES) {
        const file = scheme === 'dark' ? shot.dark : shot.file;
        mkdirSync(dirname(file), { recursive: true });
        let path = shot.path;
        if (!(await capture(shot, path, scheme))) {
          if (!shot.id.startsWith('recipes/')) {
            console.warn(`[shots] ${shot.id} (${scheme}): ${path} threw, keeping the existing file`);
            continue;
          }
          console.warn(`[shots] ${shot.id} (${scheme}): ${path} threw, taking ${DEFAULT_PAGE.path} instead`);
          path = DEFAULT_PAGE.path;
          await capture({ ...shot, waitFor: DEFAULT_PAGE.waitFor }, path, scheme);
        }
        await page.screenshot({ path: file });
        made.push(file);
        console.log(`[shots] ${shot.id} (${scheme}) <- ${path}`);
      }
    }
  } finally {
    await browser?.close();
    stop();
  }
  console.log(`[shots] wrote ${made.length} screenshot(s) under ${SHOTS_DIR}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
