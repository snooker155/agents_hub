/**
 * How one tool call went, as a dot in front of it: blinking while it runs,
 * green once it succeeded, red when it failed. The same mark on every surface
 * that draws a call (chat trail, process graph, run streams, playground), so
 * a run's state reads at a glance.
 */
import { useI18n } from '../i18n';
import { toolStatus } from './toolStatus';

const DOT = {
  running: 'bg-amber-400 animate-pulse',
  ok: 'bg-emerald-500',
  error: 'bg-red-500',
  unknown: 'bg-gray-300',
};

/** The dot itself; `entry` is the call record, or pass `status` directly. */
export default function ToolStatusMark({ entry, status, className = '' }) {
  const { t } = useI18n();
  const s = status || toolStatus(entry);
  const label = t(`chat.toolStatus.${s}`);
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-testid="tool-status"
      data-status={s}
      className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${DOT[s] || DOT.unknown} ${className}`}
    />
  );
}
