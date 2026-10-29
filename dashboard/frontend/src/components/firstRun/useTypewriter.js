/**
 * Types each phrase out a letter at a time, holds it, erases it and goes on
 * to the next, round and round, from the moment `start` turns true. Returns
 * the text shown now and whether the first phrase has been typed in full.
 * With reduced motion the first phrase simply stands.
 */
import { useEffect, useState } from 'react';

const TYPE_MS = 65;
const ERASE_MS = 32;
const HOLD_MS = 2200;
const GAP_MS = 280;

const reducedMotion = () => typeof window !== 'undefined'
  && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);

export default function useTypewriter(phrases, start) {
  const [still] = useState(reducedMotion);
  const [at, setAt] = useState({ i: 0, len: 0, erasing: false, typedOnce: false });

  useEffect(() => {
    if (!start || still || !phrases.length) return undefined;
    const phrase = phrases[at.i % phrases.length];
    let delay;
    let nextAt;
    if (!at.erasing && at.len < phrase.length) {
      delay = TYPE_MS;
      nextAt = { ...at, len: at.len + 1 };
    } else if (!at.erasing) {
      delay = HOLD_MS;
      nextAt = { ...at, erasing: true, typedOnce: true };
    } else if (at.len > 0) {
      delay = ERASE_MS;
      nextAt = { ...at, len: at.len - 1 };
    } else {
      delay = GAP_MS;
      nextAt = { ...at, i: (at.i + 1) % phrases.length, erasing: false };
    }
    const id = setTimeout(() => setAt(nextAt), delay);
    return () => clearTimeout(id);
  }, [start, still, phrases, at]);

  if (still) return { text: phrases[0] || '', typed: true };
  const phrase = phrases[at.i % phrases.length] || '';
  return { text: phrase.slice(0, at.len), typed: at.typedOnce || (at.i === 0 && at.len === phrase.length) };
}
