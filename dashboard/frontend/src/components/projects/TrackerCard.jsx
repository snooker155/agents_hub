import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, RefreshCw, Save, Ticket } from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  getProjectTracker, getTrackerStatus, setProjectTracker, syncProjectTracker, listTrackerProjects,
} from '../../api/trackers';
import { useI18n } from '../../i18n';

const PROVIDERS = ['none', 'jira', 'linear'];

// The tracker to link: a list under its button, each tracker with whether it
// is connected (credentials on the Connectors page). One not connected cannot
// be picked, unless it is the one already linked; the list's foot leads to
// the Connectors page. A list of its own rather than a native select: that
// one opened away from its button and, on a phone, in 16px type.
function ProviderPicker({ value, status, onChange, t }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  const connected = (p) => p === 'none' || Boolean(status?.[p]?.configured);
  return (
    <div ref={ref} className="relative w-full sm:w-56">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="listbox" aria-expanded={open}
        className="w-full flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
        <span className="min-w-0 flex-1 truncate text-left">{t(`projectDetails.tracker.providers.${value}`)}</span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div role="listbox" className="absolute left-0 top-full z-30 mt-1 min-w-full w-max max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-lg">
          {PROVIDERS.map((p) => {
            const ok = connected(p);
            const pickable = ok || p === value;
            return (
              <button key={p} type="button" role="option" aria-selected={p === value} disabled={!pickable}
                onClick={() => { onChange(p); setOpen(false); }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-left text-sm ${
                  pickable ? 'hover:bg-gray-50 text-gray-800' : 'cursor-not-allowed text-gray-400'}`}>
                <span className="min-w-0 flex-1 truncate">{t(`projectDetails.tracker.providers.${p}`)}</span>
                {p !== 'none' && (
                  <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                    ok ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                    <span className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-green-500' : 'bg-gray-400'}`} />
                    {ok ? t('projectDetails.tracker.connected') : t('projectDetails.tracker.notConnected')}
                  </span>
                )}
                <Check className={`h-3.5 w-3.5 shrink-0 text-indigo-600 ${p === value ? '' : 'invisible'}`} />
              </button>
            );
          })}
          <Link to="/connectors" onClick={() => setOpen(false)}
            className="mt-1 block border-t border-gray-100 px-3 pt-2 pb-1.5 text-xs text-indigo-600 hover:underline">
            {t('projectDetails.tracker.manageConnections')}
          </Link>
        </div>
      )}
    </div>
  );
}

// The project to tracker link: which Jira project or Linear team this project
// mirrors, and the button that imports its issues as tasks. Sits on the
// project's Repository tab next to the git issue sync, which it generalises.
export default function TrackerCard({ projectId, workspace }) {
  const { t } = useI18n();
  const [tracker, setTracker] = useState({ provider: 'none', remote_id: '' });
  const [fetchedChoices, setFetchedChoices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState(null);
  const [status, setStatus] = useState(null);

  useEffect(() => {
    let alive = true;
    getTrackerStatus(workspace)
      .then((r) => { if (alive) setStatus(r.data || {}); })
      .catch(() => { if (alive) setStatus({}); });
    return () => { alive = false; };
  }, [workspace]);

  useEffect(() => {
    let alive = true;
    getProjectTracker(projectId)
      .then((r) => { if (alive) setTracker({ provider: 'none', remote_id: '', ...(r.data || {}) }); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [projectId]);

  // No provider means no choices, derived rather than reset in the effect.
  const choices = tracker.provider === 'none' ? [] : fetchedChoices;

  useEffect(() => {
    if (tracker.provider === 'none') return undefined;
    let alive = true;
    listTrackerProjects(tracker.provider, workspace)
      .then((r) => { if (alive) setFetchedChoices(r.data || []); })
      .catch(() => { if (alive) setFetchedChoices([]); });
    return () => { alive = false; };
  }, [tracker.provider, workspace]);

  const save = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const { data } = await setProjectTracker(projectId, {
        provider: tracker.provider,
        remote_id: tracker.provider === 'none' ? null : (tracker.remote_id || null),
      });
      setTracker({ provider: 'none', remote_id: '', ...(data || {}) });
      setMessage({ ok: true, text: t('projectDetails.tracker.saved') });
    } catch (e) {
      setMessage({ ok: false, text: e.response?.data?.detail || e.message });
    } finally {
      setSaving(false);
    }
  };

  const sync = async () => {
    setSyncing(true);
    setMessage(null);
    try {
      const { data } = await syncProjectTracker(projectId);
      setMessage({
        ok: !data?.error,
        text: data?.error || t('projectDetails.tracker.synced', { imported: data?.imported ?? 0, updated: data?.updated ?? 0, total: data?.total ?? 0 }),
      });
    } catch (e) {
      setMessage({ ok: false, text: e.response?.data?.detail || e.message });
    } finally {
      setSyncing(false);
    }
  };

  if (loading) return null;
  const linked = tracker.provider !== 'none' && tracker.remote_id;

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
      <div className="flex items-center gap-3 flex-wrap">
        <h3 className="text-sm font-semibold text-gray-700 flex-1">
          <Ticket className="w-4 h-4 inline mr-1.5 text-gray-500" />
          {t('projectDetails.tracker.title')}
        </h3>
        {linked && (
          <button
            type="button"
            onClick={sync}
            disabled={syncing}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-indigo-200 text-indigo-700 text-xs font-medium rounded-lg hover:bg-indigo-50 disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
            {syncing ? t('projectDetails.syncing') : t('projectDetails.syncIssues')}
          </button>
        )}
      </div>
      <p className="text-xs text-gray-500">
        {t('projectDetails.tracker.hint')} <Link to="/connectors" className="text-indigo-600 hover:underline">{t('projectDetails.tracker.connectors')}</Link>
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <ProviderPicker value={tracker.provider} status={status} t={t}
          onChange={(p) => setTracker((x) => ({ ...x, provider: p, remote_id: p === x.provider ? x.remote_id : '' }))} />
        {tracker.provider !== 'none' && (
          <input
            list={`tracker-choices-${projectId}`}
            className="min-w-[10rem] flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
            value={tracker.remote_id || ''}
            onChange={(e) => setTracker((x) => ({ ...x, remote_id: e.target.value }))}
            placeholder={t(`projectDetails.tracker.remotePlaceholder.${tracker.provider}`)}
          />
        )}
        <datalist id={`tracker-choices-${projectId}`}>
          {choices.map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}
        </datalist>
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white text-xs font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
        >
          <Save className="w-3.5 h-3.5" /> {t('common.save')}
        </button>
      </div>
      {message && (
        <div className={`p-3 rounded-lg text-xs ${message.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>{message.text}</div>
      )}
    </div>
  );
}
