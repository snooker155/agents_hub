import { useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import './tour.css';
import { useI18n } from '../../i18n';
import { useFeatures } from '../features';
import { buildSteps, createTourRunner } from './tourRunner';

/**
 * The welcome tour: a popover walk through the main pages, one stop per page,
 * started from the onboarding modal or replayed from Docs. The step logic is
 * in ./tourRunner.js; this hook binds it to the router and to driver.js.
 *
 * The router's basename (the demo runs under /agents_hub/demo/) is stripped
 * from the browser path so "is this step on the current page" compares the
 * same kind of path `navigate` takes.
 */
function routerPath() {
  const base = (import.meta.env.BASE_URL || '/').replace(/\/$/, '');
  const path = window.location.pathname;
  const inner = base && path.startsWith(base) ? path.slice(base.length) : path;
  return inner || '/';
}

export function useWelcomeTour() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const features = useFeatures();
  const driverRef = useRef(null);
  const runnerRef = useRef(null);

  // A tour started from a page that then unmounts (Docs, when the tour
  // leaves it) keeps running: the runner and the driver outlive the
  // component on purpose, and only a new start() replaces them.
  const start = useCallback(() => {
    runnerRef.current?.close();
    driverRef.current?.destroy();

    const d = driver({
      animate: true,
      allowClose: true,
      overlayOpacity: 0.45,
      stagePadding: 6,
      stageRadius: 10,
      popoverClass: 'agents-hub-tour',
      // Closing with Escape or the overlay goes through the runner, so the
      // tour is marked done either way.
      onDestroyStarted: () => runnerRef.current?.close(),
    });
    driverRef.current = d;

    const steps = buildSteps(t, features);
    const runner = createTourRunner({
      steps,
      navigate: (path) => navigate(path),
      currentPath: routerPath,
      show: (step, element, c) => {
        d.highlight({
          element,
          popover: {
            title: step.title,
            description: step.description,
            showButtons: c.isFirst ? ['next', 'close'] : ['next', 'previous', 'close'],
            showProgress: true,
            progressText: t('tour.progress', { current: c.index + 1, total: c.total }),
            nextBtnText: c.isLast ? t('tour.done') : t('tour.next'),
            prevBtnText: t('tour.previous'),
            onNextClick: () => c.next(),
            onPrevClick: () => c.prev(),
            onCloseClick: () => c.close(),
          },
        });
      },
      hide: () => {
        // destroy() fires onDestroyStarted, which would call close() again;
        // the runner ignores a second finish.
        if (d.isActive()) d.destroy();
      },
    });
    runnerRef.current = runner;
    runner.start();
  }, [t, features, navigate]);

  return { start };
}

export default useWelcomeTour;
