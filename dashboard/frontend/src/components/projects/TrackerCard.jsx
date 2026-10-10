import { useEffect, useState } from 'react';
import { RefreshCw, Save, Ticket } from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  getProjectTracker, setProjectTracker, syncProjectTracker, listTrackerProjects,
} from '../../api/trackers';
import { useI18n } from '../../i18n';

const PROVIDERS = ['none', 'jira', 'linear'];

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
      <div className="grid gap-2 md:grid-cols-3">
        <select
          className="border border-gray-300 rounded-lg px-3 py-2 text-sm"
          value={tracker.provider}
          onChange={(e) => setTracker((x) => ({ ...x, provider: e.target.value, remote_id: '' }))}
        >
          {PROVIDERS.map((p) => <option key={p} value={p}>{t(`projectDetails.tracker.providers.${p}`)}</option>)}
        </select>
        {tracker.provider !== 'none' && (
          <input
            list={`tracker-choices-${projectId}`}
            className="border border-gray-300 rounded-lg px-3 py-2 text-sm"
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
          className="flex items-center justify-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white text-xs font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
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
