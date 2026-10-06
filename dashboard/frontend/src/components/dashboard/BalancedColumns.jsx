import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { bestSplit } from './split';

const WIDE = '(min-width: 1024px)'; // lg: two columns from here on

/** A card's height by its content, not by how far a column stretched it:
 *  the bottom of its last child plus its bottom padding and border. The
 *  card is `relative`, so its children's offsetTop is measured from it. */
function naturalHeight(card) {
  const last = card?.lastElementChild;
  if (!last) return card?.offsetHeight || 0;
  const style = window.getComputedStyle(card);
  return last.offsetTop + last.offsetHeight
    + parseFloat(style.paddingBottom || 0) + parseFloat(style.borderBottomWidth || 0);
}

/**
 * Cards in two columns, split so both columns hold about as much content,
 * whatever each card shows today; the last card of each column takes up the
 * few pixels left so the columns end level. One column below `lg`, in the
 * given order. `items` are `{ key, node }`; each node is a card that fills
 * its slot (`flex-1`) and is `relative`.
 */
export default function BalancedColumns({ items }) {
  const refs = useRef({});
  const [wide, setWide] = useState(() => (typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia(WIDE).matches : false));
  const [left, setLeft] = useState(null);

  const measure = useCallback(() => {
    const heights = items.map((it) => naturalHeight(refs.current[it.key]?.firstElementChild));
    if (heights.some((h) => !h)) return;
    const next = bestSplit(heights);
    setLeft((prev) => (prev && prev.length === next.length && prev.every((v, i) => v === next[i]) ? prev : next));
  }, [items]);

  useLayoutEffect(() => {
    if (!window.matchMedia) return undefined;
    const mq = window.matchMedia(WIDE);
    const onChange = () => setWide(mq.matches);
    mq.addEventListener?.('change', onChange);
    return () => mq.removeEventListener?.('change', onChange);
  }, []);

  // Content changes with every refetch and with the width: measure again.
  useLayoutEffect(() => {
    if (!wide) return undefined;
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(() => measure());
    Object.values(refs.current).forEach((el) => el?.firstElementChild && ro.observe(el.firstElementChild));
    return () => ro.disconnect();
  });

  const slot = (it, last) => (
    <div
      key={it.key}
      ref={(el) => { refs.current[it.key] = el; }}
      className={`flex flex-col min-w-0 ${last ? 'flex-1' : ''}`}
    >
      {it.node}
    </div>
  );

  if (!wide || !left || left.length !== items.length) {
    return <div className="grid grid-cols-1 gap-6">{items.map((it) => slot(it, false))}</div>;
  }
  const cols = [items.filter((_, i) => left[i]), items.filter((_, i) => !left[i])];
  return (
    <div className="grid grid-cols-2 gap-6" data-testid="dash-balanced">
      {cols.map((col, c) => (
        <div key={c} className="flex flex-col gap-6 min-w-0">
          {col.map((it, i) => slot(it, i === col.length - 1))}
        </div>
      ))}
    </div>
  );
}
