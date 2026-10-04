import { useEffect, useRef, useState } from 'react';

/*
 * `target`, held: once a value is on show it stays for at least `minMs`, and
 * a value that arrives sooner waits for the rest of that time. Values that
 * come and go while it waits are skipped; only the latest one is shown.
 */
export default function useSteadyState(target, minMs = 700) {
  const [shown, setShown] = useState(target);
  const since = useRef(0);

  // The first value is on show from mount.
  useEffect(() => { since.current = Date.now(); }, []);

  useEffect(() => {
    if (target === shown) return undefined;
    const wait = Math.max(0, minMs - (Date.now() - since.current));
    const id = setTimeout(() => {
      since.current = Date.now();
      setShown(target);
    }, wait);
    return () => clearTimeout(id);
  }, [target, shown, minMs]);

  return shown;
}
