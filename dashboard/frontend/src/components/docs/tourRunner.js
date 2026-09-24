/**
 * The welcome tour's logic, free of React and of driver.js so it can be
 * tested with fake timers (see __tests__/WelcomeTour.test.jsx). WelcomeTour.jsx
 * binds it to react-router and to a driver.js popover.
 *
 * A step is `{ id, path, element, title, description }`: the page it lives on,
 * a CSS selector on that page, and the popover text. Steps on another page
 * navigate first and then wait for the element, because a page renders its
 * content after its data arrives. An element that never shows up within the
 * timeout is skipped rather than stalling the tour.
 */

export const TOUR_DONE_KEY = 'agents_hub_tour_done_v1';
export const ELEMENT_TIMEOUT_MS = 3000;

/**
 * The tour's stops, in order. Selectors are ones the pages already render:
 * every page has a PageHeader (a <header> inside <main>), Chat has its
 * composer textarea, and Agents its card grid. The playground stop exists
 * only when the backend has the playground enabled.
 */
export const TOUR_STOPS = [
  { id: 'chat', path: '/chat', element: 'main textarea' },
  { id: 'agents', path: '/agents', element: 'main .grid' },
  { id: 'tasks', path: '/tasks', element: 'main header' },
  { id: 'flows', path: '/flows', element: 'main header' },
  { id: 'teams', path: '/teams', element: 'main header' },
  { id: 'playground', path: '/playground', element: 'main header', feature: 'playground' },
  { id: 'views', path: '/views', element: 'main header' },
  { id: 'health', path: '/health', element: 'main header' },
  { id: 'docs', path: '/docs', element: 'main header' },
];

/** The stops for this deployment, with their text resolved through `t`. */
export function buildSteps(t, features = {}) {
  return TOUR_STOPS
    .filter((s) => !s.feature || features[s.feature] !== false)
    .map((s) => ({
      id: s.id,
      path: s.path,
      element: s.element,
      title: t(`tour.steps.${s.id}.title`),
      description: t(`tour.steps.${s.id}.description`),
    }));
}

/**
 * Resolve with the first element matching `selector`, or null after
 * `timeout` ms. Watches the DOM rather than polling, so a page that renders
 * its content late is picked up the moment it does.
 */
export function waitForElement(selector, { timeout = ELEMENT_TIMEOUT_MS, root = document } = {}) {
  const found = root.querySelector(selector);
  if (found) return Promise.resolve(found);
  return new Promise((resolve) => {
    let done = false;
    const finish = (el) => {
      if (done) return;
      done = true;
      observer.disconnect();
      clearTimeout(timer);
      resolve(el);
    };
    const observer = new MutationObserver(() => {
      const el = root.querySelector(selector);
      if (el) finish(el);
    });
    observer.observe(root.body || root.documentElement || root, { childList: true, subtree: true });
    const timer = setTimeout(() => finish(root.querySelector(selector)), timeout);
  });
}

export function markTourDone(storage = globalThis.localStorage) {
  try { storage?.setItem(TOUR_DONE_KEY, '1'); } catch { /* storage unavailable */ }
}

export function isTourDone(storage = globalThis.localStorage) {
  try { return storage?.getItem(TOUR_DONE_KEY) === '1'; } catch { return false; }
}

/**
 * Drive `steps` one at a time.
 *
 * - `navigate(path)` and `currentPath()` move between pages.
 * - `show(step, element, controls)` renders the popover; `controls` holds
 *   `next`, `prev`, `close`, `index` and `total` (counting only the steps
 *   that were actually found is not possible up front, so `total` is the
 *   full list).
 * - `hide()` removes it.
 *
 * Returns `{ start, next, prev, close, index }`. Finishing the last step or
 * closing the tour marks it done in localStorage, so it is not offered
 * again as new; the Docs page can still replay it.
 */
export function createTourRunner({
  steps, navigate, currentPath, show, hide, wait = waitForElement, storage,
  onFinish,
}) {
  let index = -1;
  let token = 0;
  let finished = false;

  const finish = (completed) => {
    if (finished) return;
    finished = true;
    token += 1;
    hide?.();
    markTourDone(storage);
    onFinish?.(completed);
  };

  const go = async (target, direction) => {
    if (finished) return;
    const mine = ++token;
    let i = target;
    while (i >= 0 && i < steps.length) {
      const step = steps[i];
      if (step.path && currentPath() !== step.path) navigate(step.path);
      const el = await wait(step.element);
      if (mine !== token || finished) return;
      if (el) {
        index = i;
        show(step, el, {
          index: i,
          total: steps.length,
          isFirst: i === 0,
          isLast: i === steps.length - 1,
          next: () => runner.next(),
          prev: () => runner.prev(),
          close: () => runner.close(),
        });
        return;
      }
      // Not on the page within the timeout: skip it in the direction of travel.
      i += direction;
    }
    if (i >= steps.length) finish(true);
    // Walking back past the first step: stay where we are.
  };

  const runner = {
    start: () => go(0, 1),
    next: () => (index >= steps.length - 1 ? finish(true) : go(index + 1, 1)),
    prev: () => (index <= 0 ? undefined : go(index - 1, -1)),
    close: () => finish(false),
    get index() { return index; },
    get finished() { return finished; },
  };
  return runner;
}
