import { useEffect, useState } from 'react';
import { Globe, Loader } from 'lucide-react';
import { getAgentWebDomains, updateAgentWebDomains } from '../../api/agentWebDomains';
import { useWorkspace } from '../workspace';
import { useI18n } from '../../i18n';

const linesOf = (list) => (list || []).join('\n');
const listOf = (text) => String(text || '').split(/[\n,\s]+/).map((x) => x.trim()).filter(Boolean);

/**
 * The agent's own domain lists for web_search, fetch_url and the browser
 * (`AgentSpec.allowed_domains` / `blocked_domains`, tools/web.py).
 *
 * Blocked hosts join the workspace's deny list; allowed hosts, when set,
 * narrow the agent to them on top of whatever the workspace allows. A
 * blocked host wins over an allowed one, and a host covers its subdomains.
 * Below the fields the card shows what the current workspace adds, so the
 * operator sees the lists a run will actually be held to.
 */
export default function AgentWebDomainsCard({ agentId, readOnly = false }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [saved, setSaved] = useState({ allowed: '', blocked: '' });
  const [allowed, setAllowed] = useState('');
  const [blocked, setBlocked] = useState('');
  const [effective, setEffective] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getAgentWebDomains(agentId, selectedWorkspace || undefined)
      .then(({ data }) => {
        if (cancelled) return;
        const a = linesOf(data?.allowed_domains);
        const b = linesOf(data?.blocked_domains);
        setSaved({ allowed: a, blocked: b });
        setAllowed(a);
        setBlocked(b);
        setEffective(data?.effective || null);
      })
      .catch(() => { if (!cancelled) setError(t('webDomains.loadFailed')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [agentId, selectedWorkspace, t]);

  const dirty = allowed !== saved.allowed || blocked !== saved.blocked;

  const save = async () => {
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const { data } = await updateAgentWebDomains(agentId, {
        allowed_domains: listOf(allowed),
        blocked_domains: listOf(blocked),
      });
      const a = linesOf(data?.allowed_domains);
      const b = linesOf(data?.blocked_domains);
      setSaved({ allowed: a, blocked: b });
      setAllowed(a);
      setBlocked(b);
      setMessage(t('webDomains.saved'));
    } catch (err) {
      setError(err?.response?.data?.detail || t('webDomains.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const areaCls = 'w-full border border-gray-200 rounded-lg px-3 py-2 text-xs font-mono min-h-[5rem] '
    + 'focus:ring-2 focus:ring-indigo-500 focus:outline-none disabled:bg-gray-50';
  const wsBlocked = (effective?.blocked || []).filter((h) => !listOf(saved.blocked).includes(h));

  return (
    <div className="bg-white p-6 shadow-md rounded-lg" data-testid="agent-web-domains">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <Globe className="w-5 h-5 text-indigo-600" />
          <h3 className="text-lg font-bold text-gray-900">{t('webDomains.title')}</h3>
        </div>
        {!readOnly && (
          <button
            type="button"
            onClick={save}
            disabled={saving || !dirty}
            className={`px-4 py-2 rounded-lg text-sm font-semibold ${
              saving || !dirty
                ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                : 'bg-indigo-600 text-white hover:bg-indigo-700'
            }`}
          >
            {saving ? t('common.saving') : t('webDomains.save')}
          </button>
        )}
      </div>
      <p className="text-sm text-gray-600 mb-4">{t('webDomains.intro')}</p>
      {loading ? (
        <Loader className="w-4 h-4 animate-spin text-gray-400" />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor={`wd-allowed-${agentId}`}>
              {t('webDomains.allowed')}
            </label>
            <textarea id={`wd-allowed-${agentId}`} value={allowed} disabled={readOnly}
              onChange={(e) => setAllowed(e.target.value)} placeholder={'docs.python.org\narxiv.org'}
              className={areaCls} />
            <p className="text-xs text-gray-500 mt-1">{t('webDomains.allowedHint')}</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor={`wd-blocked-${agentId}`}>
              {t('webDomains.blocked')}
            </label>
            <textarea id={`wd-blocked-${agentId}`} value={blocked} disabled={readOnly}
              onChange={(e) => setBlocked(e.target.value)} placeholder={'tracker.example'}
              className={areaCls} />
            <p className="text-xs text-gray-500 mt-1">{t('webDomains.blockedHint')}</p>
          </div>
        </div>
      )}
      {effective && (
        <div className="mt-3 text-xs text-gray-500 space-y-1" data-testid="web-domains-effective">
          <p>
            {t('webDomains.workspaceBlocked')}{' '}
            <span className="font-mono">{wsBlocked.length ? wsBlocked.join(', ') : t('webDomains.none')}</span>
          </p>
          <p>
            {t('webDomains.workspaceAllowed')}{' '}
            <span className="font-mono">
              {effective.allowed == null ? t('webDomains.anyHost') : (effective.allowed.join(', ') || t('webDomains.none'))}
            </span>
          </p>
        </div>
      )}
      {message && <p className="mt-3 text-xs text-emerald-700">{message}</p>}
      {error && <p className="mt-3 text-xs text-red-600">{error}</p>}
    </div>
  );
}
