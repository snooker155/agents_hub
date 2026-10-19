/**
 * Shows the first run (FirstRun.jsx, docs/installation.md, "The first run in the browser") in place of the
 * whole app until it is finished, the same way AuthGate shows the login
 * screen: no shell behind it and no route past it.
 *
 * Never in the recorded demo. An unreachable
 * backend or an older one without the route lets the app through: the first
 * run must never be what keeps someone out of a working hub.
 *
 * Also hands a second browser the language and look chosen in the first run,
 * when that browser has none of its own yet.
 */
import { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import PageLoader from '../PageLoader';
import { useI18n } from '../../i18n';
import { useTheme } from '../theme';
import { getFirstRun } from '../../api/firstRun';
import { useWelcomeTour } from '../docs/WelcomeTour';

const FirstRun = lazy(() => import('./FirstRun'));

const LANGUAGE_KEY = 'agents_hub_language';
const THEME_KEY = 'theme';

function stored(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function Fallback() {
  return (
    <div className="h-screen w-full flex items-center justify-center bg-gray-50">
      <PageLoader size="lg" />
    </div>
  );
}

export default function FirstRunGate({ children }) {
  const navigate = useNavigate();
  const tour = useWelcomeTour();
  const { setLanguage } = useI18n();
  const { setTheme } = useTheme();
  const [state, setState] = useState(null);
  const [after, setAfter] = useState('');
  // Read on mount, before anything here can have written them.
  const own = useRef({ language: stored(LANGUAGE_KEY), theme: stored(THEME_KEY) });

  useEffect(() => {
    let live = true;
    getFirstRun()
      .then(({ data }) => {
        if (!live) return;
        if (data?.language && !own.current.language) setLanguage(data.language);
        if (data?.theme && !own.current.theme) setTheme(data.theme);
        setState(data || { required: false });
      })
      .catch(() => { if (live) setState({ required: false }); });
    return () => { live = false; };
    // setTheme is a new function each render of ThemeProvider; one read is enough.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The tour points at the sidebar and header, so it starts once they are drawn.
  useEffect(() => {
    if (after !== 'tour' || state?.required) return undefined;
    const id = setTimeout(() => { setAfter(''); tour.start(); }, 400);
    return () => clearTimeout(id);
  }, [after, state, tour]);

  if (!state) return <Fallback />;
  if (!state.required) return children;

  const onFinish = (target) => {
    navigate('/chat', { replace: true });
    if (target === 'tour') setAfter('tour');
    setState({ ...state, required: false });
  };

  return (
    <Suspense fallback={<Fallback />}>
      <FirstRun initial={state} onFinish={onFinish} />
    </Suspense>
  );
}
