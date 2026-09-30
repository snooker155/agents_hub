import { AlertCircle, CheckCircle, Clock, Loader, Square, XCircle } from 'lucide-react';
import { useI18n, statusLabel } from '../i18n';

// A run's or a session's status as a pill: the Messages and Sessions lists
// and the agent page's Runs and Sessions tabs all draw it the same way.
const STATUS_STYLES = {
  running:   { bg: 'bg-blue-100',   text: 'text-blue-700',   icon: Loader },
  completed: { bg: 'bg-green-100',  text: 'text-green-700',  icon: CheckCircle },
  failed:    { bg: 'bg-red-100',    text: 'text-red-700',    icon: XCircle },
  error:     { bg: 'bg-red-100',    text: 'text-red-700',    icon: XCircle },
  stopped:   { bg: 'bg-gray-100',   text: 'text-gray-600',   icon: Square },
  stop:      { bg: 'bg-orange-100', text: 'text-orange-700', icon: Square },
  pending:   { bg: 'bg-gray-100',   text: 'text-gray-500',   icon: Clock },
};

export default function RunStatusBadge({ status }) {
  const { t } = useI18n();
  const s = STATUS_STYLES[status] || { bg: 'bg-gray-100', text: 'text-gray-500', icon: AlertCircle };
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <Icon className={`w-3 h-3 ${status === 'running' ? 'animate-spin' : ''}`} />
      {statusLabel(status, t)}
    </span>
  );
}
