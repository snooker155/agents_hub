/**
 * Demo bootstrap: loads the recorded fixtures and starts the MSW service
 * worker before the app renders. Imported dynamically from main.jsx only when
 * the bundle was built with VITE_DEMO=1, so a normal build never ships MSW or
 * the fixtures.
 */
import { setupWorker } from 'msw/browser';
import { createHandlers } from './handlers';

// A glob rather than a static import: a build made before the fixtures were
// recorded still succeeds and serves the shaped fallbacks.
const FIXTURE_FILES = import.meta.glob('./fixtures/*.json', { import: 'default' });

async function loadJson(name) {
  const load = FIXTURE_FILES[`./fixtures/${name}.json`];
  if (!load) return {};
  try {
    return (await load()) || {};
  } catch {
    return {};
  }
}

export const DEMO_WORKSPACE = 'demo';

export async function startDemo() {
  const [fixtures, streams] = await Promise.all([loadJson('fixtures'), loadJson('streams')]);
  const worker = setupWorker(...createHandlers({ fixtures, streams }));
  await worker.start({
    onUnhandledRequest: 'bypass',
    quiet: true,
    serviceWorker: { url: `${import.meta.env.BASE_URL}mockServiceWorker.js` },
  });
  try {
    document.documentElement.setAttribute('data-demo', '1');
    // Land a first visit in the recorded workspace rather than an empty one.
    if (!localStorage.getItem('selectedWorkspace')) {
      localStorage.setItem('selectedWorkspace', fixtures.workspace || DEMO_WORKSPACE);
    }
  } catch {
    // No storage: the workspace selector starts empty, which still works.
  }
  // GitHub Pages serves the demo at .../demo/index.html; the router knows
  // nothing about that file name.
  if (window.location.pathname.endsWith('/index.html')) {
    window.history.replaceState(null, '', window.location.pathname.replace(/index\.html$/, '') + window.location.search + window.location.hash);
  }
  return worker;
}
