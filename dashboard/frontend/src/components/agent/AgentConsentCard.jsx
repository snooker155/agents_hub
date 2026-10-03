import React, { useCallback, useEffect, useState } from 'react';
import { Loader, ShieldCheck } from 'lucide-react';
import {
  getConsentSettings, listConsentGrants, revokeConsentGrant, updateConsentSettings,
} from '../../api/consent';
import { useWorkspace } from '../workspace';
import { useI18n } from '../../i18n';

const sameList = (a, b) => (a || []).length === (b || []).length
  && (a || []).every((x, i) => x === (b || [])[i]);

/**
 * Account access (the consent portal, docs/consent.md): which providers this
 * agent acts on as the end user of a widget or chat channel, and with which
 * access, plus the personal grants end users gave it in the current
 * workspace, each with a Revoke button.
 *
 * A provider switched on here means: in a widget or channel turn the agent's
 * Google or Microsoft tools act only as the person in the conversation, and
 * refuse with "ask for access first" until that person grants it through the
 * link the agent sends. The redirect URI to register with Google and
 * Microsoft is shown from the backend's own answer.
 */
export default function AgentConsentCard({ agentId, readOnly = false }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const workspace = selectedWorkspace || 'default';
  const [catalog, setCatalog] = useState(null);
  const [providers, setProviders] = useState([]);
  const [scopes, setScopes] = useState({});
  const [saved, setSaved] = useState({ providers: [], scopes: {} });
  const [grants, setGrants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [revoking, setRevoking] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const apply = (data) => {
    const p = Array.isArray(data?.providers) ? data.providers : [];
    const s = data?.scopes || {};
    setProviders(p);
    setScopes(s);
    setSaved({ providers: p, scopes: s });
    if (data?.catalog) setCatalog(data.catalog);
  };

  const loadGrants = useCallback(() => listConsentGrants(workspace, agentId)
    .then(({ data }) => setGrants(Array.isArray(data?.grants) ? data.grants : []))
    .catch(() => setGrants([])), [workspace, agentId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getConsentSettings(agentId)
      .then(({ data }) => { if (!cancelled) apply(data); })
      .catch(() => { if (!cancelled) setError(t('consent.loadFailed')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [agentId, t]);

  useEffect(() => { loadGrants(); }, [loadGrants]);

  const toggleProvider = (provider, defaults) => {
    setMessage('');
    if (providers.includes(provider)) {
      setProviders(providers.filter((p) => p !== provider));
      return;
    }
    setProviders([...providers, provider]);
    if (!(scopes[provider] || []).length) setScopes({ ...scopes, [provider]: [...(defaults || [])] });
  };

  const toggleAccess = (provider, key, offered) => {
    setMessage('');
    const current = scopes[provider] || [];
    const next = current.includes(key) ? current.filter((k) => k !== key) : [...current, key];
    setScopes({ ...scopes, [provider]: offered.filter((k) => next.includes(k)) });
  };

  const dirty = !sameList(providers, saved.providers)
    || (catalog?.providers || []).some((p) => !sameList(scopes[p.id], saved.scopes[p.id]));

  const save = async () => {
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const { data } = await updateConsentSettings(agentId, { providers, scopes });
      apply(data);
      setMessage(t('consent.saved'));
    } catch (err) {
      setError(err?.response?.data?.detail || t('consent.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const revoke = async (grant) => {
    if (!window.confirm(t('consent.revokeConfirm', { account: grant.account_email || grant.principal }))) return;
    setRevoking(grant.request_id);
    setError('');
    try {
      await revokeConsentGrant(grant.request_id, workspace);
      await loadGrants();
    } catch (err) {
      setError(err?.response?.data?.detail || t('consent.revokeFailed'));
    } finally {
      setRevoking('');
    }
  };

  return (
    <div className="bg-white p-6 shadow-md rounded-lg" data-testid="agent-consent">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-indigo-600" />
          <h3 className="text-lg font-bold text-gray-900">{t('consent.title')}</h3>
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
            {saving ? t('common.saving') : t('consent.save')}
          </button>
        )}
      </div>
      <p className="text-sm text-gray-600 mb-4">{t('consent.intro')}</p>
      {loading ? (
        <Loader className="w-4 h-4 animate-spin text-gray-400" />
      ) : (
        <div className="space-y-4">
          {(catalog?.providers || []).map((p) => {
            const on = providers.includes(p.id);
            return (
              <div key={p.id} className="border border-gray-200 rounded-lg p-3">
                <label className="flex items-center gap-2 text-sm font-medium text-gray-800">
                  <input type="checkbox" checked={on} disabled={readOnly}
                    onChange={() => toggleProvider(p.id, p.default)} aria-label={p.label} />
                  {t('consent.actAs', { provider: p.label })}
                </label>
                {!p.ready && (
                  <p className="text-xs text-amber-700 mt-1">{t('consent.notReady', { provider: p.label })}</p>
                )}
                {on && (
                  <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-1">
                    {p.access.map((key) => (
                      <label key={key} className="flex items-center gap-2 text-xs text-gray-700">
                        <input type="checkbox" disabled={readOnly}
                          checked={(scopes[p.id] || []).includes(key)}
                          onChange={() => toggleAccess(p.id, key, p.access)} />
                        {t(`consent.access.${key}`)}
                      </label>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          {catalog?.redirect_uri && (
            <p className="text-xs text-gray-500">
              {t('consent.redirectUri')}{' '}
              <span className="font-mono select-all" data-testid="consent-redirect-uri">{catalog.redirect_uri}</span>
            </p>
          )}
        </div>
      )}

      <div className="mt-5">
        <h4 className="text-sm font-semibold text-gray-800 mb-2">{t('consent.grantsTitle')}</h4>
        {grants.length === 0 ? (
          <p className="text-xs text-gray-500">{t('consent.noGrants')}</p>
        ) : (
          <ul className="divide-y divide-gray-100 border border-gray-200 rounded-lg">
            {grants.map((g) => (
              <li key={g.request_id} className="flex items-center justify-between gap-3 px-3 py-2 text-xs">
                <div className="min-w-0">
                  <div className="font-medium text-gray-800 truncate">
                    {g.account_email || t('consent.unknownAccount')}
                    <span className="text-gray-500"> · {g.provider === 'microsoft' ? 'Microsoft' : 'Google'}</span>
                  </div>
                  <div className="text-gray-500 truncate">
                    {t(`consent.via.${g.principal_kind || 'other'}`)} · <span className="font-mono">{g.principal}</span>
                  </div>
                  <div className="text-gray-500 truncate">
                    {(g.access || []).map((k) => t(`consent.access.${k}`)).join(', ')}
                  </div>
                </div>
                {!readOnly && (
                  <button type="button" onClick={() => revoke(g)} disabled={revoking === g.request_id}
                    className="px-3 py-1 rounded-md border border-red-200 text-red-700 hover:bg-red-50 disabled:opacity-50">
                    {revoking === g.request_id ? t('consent.revoking') : t('consent.revoke')}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      {message && <p className="mt-3 text-xs text-emerald-700">{message}</p>}
      {error && <p className="mt-3 text-xs text-red-600">{error}</p>}
    </div>
  );
}
