import { lazy, Suspense } from 'react';

// xterm.js and its stylesheet load only when a terminal is actually opened,
// not with every run page and service page.
const TerminalPanel = lazy(() => import('./TerminalPanel'));

export default function LazyTerminalPanel(props) {
  return (
    <Suspense fallback={null}>
      <TerminalPanel {...props} />
    </Suspense>
  );
}
