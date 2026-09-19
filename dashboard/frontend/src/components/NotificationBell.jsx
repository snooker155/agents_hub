import { useState, useEffect, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useWorkspace } from './workspace';
import {
  getNotifications,
  getNotificationsUnreadCount,
  markNotificationRead,
  markAllNotificationsRead,
} from '../api';
import { useStreamEvent } from './stream';
import { Bell, MailOpen, Loader } from 'lucide-react';
import { useI18n } from '../i18n';
import { useToast, errorDetail } from './toast';

function timeAgo(iso, t) {
  if (!iso) return '';
  const mins = Math.max(0, Math.round((new Date() - new Date(iso)) / 60000));
  if (mins < 1) return t('components.notifications.justNow');
  if (mins < 60) return t('components.notifications.minutesAgo', { count: mins });
  if (mins < 60 * 24) return t('components.notifications.hoursAgo', { count: Math.round(mins / 60) });
  return t('components.notifications.daysAgo', { count: Math.round(mins / (60 * 24)) });
}

export default function NotificationBell() {
  const { workspaceFilter } = useWorkspace();
  const { t } = useI18n();
  const toast = useToast();
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const dropdownRef = useRef(null);
  // Ids already counted into the badge, so a redelivered push is a no-op.
  const seenIdsRef = useRef(new Set());

  const refreshCount = useCallback(() => {
    getNotificationsUnreadCount(workspaceFilter)
      .then(({ data }) => setUnread(data.unread || 0))
      .catch(() => {});
  }, [workspaceFilter]);

  const fetchRecent = useCallback(() => {
    setLoading(true);
    getNotifications({ ...(workspaceFilter ? { workspace: workspaceFilter } : {}), limit: 12 })
      .then(({ data }) => setItems(data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [workspaceFilter]);

  // Unread badge: initial load; live push (below) keeps it current thereafter.
  useEffect(() => { refreshCount(); }, [refreshCount]);

  // Live push over the shared multiplexed stream.
  useStreamEvent('__notifications__', 'notification', (event) => {
    const n = event.notification;
    if (!n) return;
    // Same rule the server's unread-count applies: a notification with no
    // workspace belongs to no filtered view. Treating it as a match here made
    // the badge count rows the list would never show, so the number jumped
    // back down the moment the dropdown refetched.
    if (workspaceFilter && (n.workspace || '') !== workspaceFilter) return;
    // A redelivered event must not add a second row (duplicate React key) or a
    // second point on the badge. The guard lives in a ref rather than in the
    // setItems updater: the badge is a separate piece of state, and the list
    // only keeps the newest 12, so an id that scrolled off would count twice.
    if (seenIdsRef.current.has(n.id)) return;
    seenIdsRef.current.add(n.id);
    if (seenIdsRef.current.size > 500) seenIdsRef.current.clear();
    setUnread(u => u + 1);
    setItems(prev => (prev.some(i => i.id === n.id) ? prev : [n, ...prev].slice(0, 12)));
  });

  // Close on outside click
  useEffect(() => {
    const handler = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const toggle = () => {
    setOpen(o => {
      if (!o) { fetchRecent(); refreshCount(); }
      return !o;
    });
  };

  const handleMarkRead = async (n) => {
    try {
      await markNotificationRead(n.id, true);
      setItems(prev => prev.map(i => (i.id === n.id ? { ...i, read: true } : i)));
      setUnread(u => Math.max(0, u - 1));
    } catch (e) {
      // The badge keeps its unread count, so say why nothing moved.
      toast.error(t('components.notifications.markReadFailed'), errorDetail(e));
    }
  };

  const handleMarkAll = async () => {
    try {
      await markAllNotificationsRead(workspaceFilter);
      setItems(prev => prev.map(i => ({ ...i, read: true })));
      setUnread(0);
    } catch (e) {
      toast.error(t('components.notifications.markReadFailed'), errorDetail(e));
    }
  };

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={toggle}
        title={t('components.notifications.tooltip')}
        className="relative p-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 hover:text-gray-900 transition-colors"
      >
        <Bell className="w-4 h-4" />
        {unread > 0 && (
          <span className="absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center justify-center">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-96 bg-white border border-gray-200 rounded-xl shadow-lg z-50 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-100">
            <span className="text-sm font-semibold text-gray-900">{t('components.notifications.title')}</span>
            {unread > 0 && (
              <button onClick={handleMarkAll} className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800">
                <MailOpen className="w-3.5 h-3.5" /> {t('components.notifications.markAllRead')}
              </button>
            )}
          </div>

          <div className="max-h-96 overflow-y-auto divide-y divide-gray-50">
            {loading ? (
              <div className="flex justify-center py-8"><Loader className="w-5 h-5 animate-spin text-indigo-500" /></div>
            ) : items.length === 0 ? (
              <div className="text-center py-8">
                <Bell className="w-8 h-8 text-gray-200 mx-auto mb-2" />
                <p className="text-xs text-gray-400">{t('components.notifications.empty')}</p>
              </div>
            ) : (
              items.map(n => (
                <div
                  key={n.id}
                  onClick={() => { if (!n.read) handleMarkRead(n); }}
                  className={`px-4 py-2.5 cursor-pointer hover:bg-gray-50 ${n.read ? '' : 'bg-indigo-50/40'}`}
                >
                  <div className="flex items-start gap-2">
                    <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${n.read ? 'bg-transparent' : 'bg-indigo-500'}`} />
                    <div className="min-w-0 flex-1">
                      <div className={`text-sm truncate ${n.read ? 'text-gray-600' : 'font-semibold text-gray-900'}`}>{n.title}</div>
                      {n.body && <div className="text-xs text-gray-500 mt-0.5 line-clamp-2">{n.body}</div>}
                      <div className="text-[11px] text-gray-400 mt-0.5 flex items-center gap-2">
                        <span>{timeAgo(n.created_at, t)}</span>
                        {n.source?.task_id && (
                          <Link
                            to={`/tasks/${n.source.task_id}`}
                            onClick={(e) => { e.stopPropagation(); setOpen(false); }}
                            className="text-indigo-500 hover:underline"
                          >
                            {t('components.notifications.viewTask')}
                          </Link>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          <Link
            to="/plan?tab=notifications"
            onClick={() => setOpen(false)}
            className="block text-center text-xs font-medium text-indigo-600 hover:bg-indigo-50 px-4 py-2.5 border-t border-gray-100"
          >
            {t('components.notifications.viewAllInPlan')}
          </Link>
        </div>
      )}
    </div>
  );
}
