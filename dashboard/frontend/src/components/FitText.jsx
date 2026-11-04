import { useLayoutEffect, useRef } from 'react';

/**
 * A one-line name that steps its font down until it fits its box: tried at
 * each size of `sizes` (px, largest first) and left at the first that fits,
 * else at the last, where `truncate` cuts it with an ellipsis. Measured again
 * whenever the box changes width. The class sets everything but the size.
 */
export default function FitText({ children, sizes = [16, 14, 13, 12, 11], className = '', ...rest }) {
  const ref = useRef(null);
  const key = sizes.join(',');
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const steps = key.split(',').map(Number);
    const fit = () => {
      for (const px of steps) {
        el.style.fontSize = `${px}px`;
        if (el.scrollWidth <= el.clientWidth) return;
      }
    };
    fit();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(fit);
    observer.observe(el);
    return () => observer.disconnect();
  }, [children, key]);
  return <span ref={ref} className={`truncate ${className}`} {...rest}>{children}</span>;
}
