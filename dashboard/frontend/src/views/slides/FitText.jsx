import React, { useLayoutEffect, useRef } from 'react';

/**
 * A box whose font size is the largest from `base` down to `min` (px) at which
 * its content fits. Content sizes itself in `em`, so only this box's font size
 * changes; it is set on the element directly, before paint, and so travels with
 * the HTML when the deck is copied into the PDF print window. Measures again
 * when `contentKey` or the size limits change.
 */
export default function FitText({ base, min = 12, contentKey, style, children }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    let size = base;
    el.style.fontSize = `${size}px`;
    while (size > min && el.scrollHeight > el.clientHeight + 1) {
      size -= 1;
      el.style.fontSize = `${size}px`;
    }
  }, [base, min, contentKey]);
  return <div ref={ref} style={{ overflow: 'hidden', minHeight: 0, fontSize: base, ...style }}>{children}</div>;
}
