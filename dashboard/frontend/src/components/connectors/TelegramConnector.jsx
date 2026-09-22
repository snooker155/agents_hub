import { useCallback, useEffect, useState } from 'react';
import { MessageSquare, RefreshCw, Save, Trash2, Wifi } from 'lucide-react';
import {
  getTelegramConfig, updateTelegramConfig, testTelegramToken,
  getTelegramStatus, getTelegramBindings, deleteTelegramBinding,
} from '../../api';
import { SectionCard, inputCls } from '../settingsUi';
import { useI18n } from '../../i18n';
import { useLiveRefetch } from '../stream';

// Moved out of the Settings page, which is where nobody looked for it: a
// connector is something you *attach*, so it belongs with the other things you
// attach (Connect → Connectors), not with model keys and log levels.
//
// The strings still live under the `settings.*` i18n keys they were written
// with. They are the same strings, moved verbatim; renaming a hundred keys
// across three locales in the same change as a page move would bury the move
// in the diff. That rename is mechanical and the parity test guards it, so it
// can happen on its own.

// ── Telegram tab ─────────────────────────────────────────────────────────────

export default function TelegramConnector() {
  const { t } = useI18n();
  const [loading, setLoading] = useState(true);
  const [config, setConfig] = useState({ enabled: false, has_token: false, bot_username: null, running: false });
  const [status, setStatus] = useState({});
  const [bindings, setBindings] = useState([]);
  const [tokenInput, setTokenInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [cfgResp, statusResp, bindingsResp] = await Promise.all([
        getTelegramConfig(),
        getTelegramStatus(),
        getTelegramBindings(),
      ]);
      setConfig(cfgResp.data);
      setStatus(statusResp.data);
      setBindings(bindingsResp.data || []);
    } catch (e) {
      setError(`${t('settings.errors.telegramLoad')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  // The poller's liveness (running, last_poll, last_error) changes from its
  // own background loop, not from anything this tab did, so it needs a live
  // update rather than a fixed refresh point. The backend publishes
  // `telegram.changed` on the app channel for exactly this; a save already
  // refreshes the same fields outright, so this only needs to cover changes
  // from elsewhere.
  const refreshStatus = useCallback(async () => {
    try {
      const { data } = await getTelegramStatus();
      setStatus(data);
      setConfig((c) => ({ ...c, running: data.running, bot_username: data.bot_username }));
    } catch { /* a failed read just waits for the next event */ }
  }, []);
  useLiveRefetch(refreshStatus, { type: 'telegram.changed' });

  const handleSave = async ({ enabled, clear_token } = {}) => {
    setSaving(true);
    setError('');
    setTestResult(null);
    try {
      const payload = {};
      if (tokenInput.trim()) payload.bot_token = tokenInput.trim();
      if (clear_token) payload.clear_token = true;
      if (enabled !== undefined) payload.enabled = enabled;
      const { data } = await updateTelegramConfig(payload);
      setConfig(data);
      setTokenInput('');
      // Refresh status + bindings after a save (poller may have just started/stopped).
      const [statusResp, bindingsResp] = await Promise.all([
        getTelegramStatus(),
        getTelegramBindings(),
      ]);
      setStatus(statusResp.data);
      setBindings(bindingsResp.data || []);
    } catch (e) {
      setError(`${t('settings.errors.save')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const { data } = await testTelegramToken();
      setTestResult(data);
    } catch (e) {
      setTestResult({ ok: false, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  const handleDeleteBinding = async (chatId) => {
    if (!window.confirm(t('settings.confirmRemoveBinding', { chatId }))) return;
    try {
      await deleteTelegramBinding(chatId);
      setBindings((bs) => bs.filter((b) => b.chat_id !== chatId));
    } catch (e) {
      setError(`${t('settings.errors.removeBinding')}: ` + (e.response?.data?.detail || e.message));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <RefreshCw className="w-5 h-5 animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}

      <SectionCard title={t('settings.telegramBot')}>
        <p className="text-sm text-gray-600">
          {t('settings.telegram.introBefore')} <code className="text-xs bg-gray-100 rounded px-1">/agent &lt;id&gt;</code>{t('settings.telegram.introAfter')}
        </p>

        <div>
          <div className="flex items-center gap-2 mb-1">
            <label className="text-sm font-medium text-gray-700">
              {t('settings.telegram.botToken')} {config.has_token && <span className="text-gray-400 font-normal">({t('settings.currentlySet')})</span>}
            </label>
          </div>
          <input
            type="password"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder={config.has_token ? t('settings.keepExistingToken') : t('settings.telegram.pasteBotToken')}
            className={inputCls}
            autoComplete="new-password"
          />
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <button
              type="button"
              onClick={() => handleSave({})}
              disabled={saving || !tokenInput.trim()}
              className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
            >
              {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              {t('settings.saveToken')}
            </button>
            <button
              type="button"
              onClick={handleTest}
              disabled={testing || !config.has_token}
              className="flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 disabled:opacity-50"
            >
              {testing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
              {t('settings.testConnection')}
            </button>
            {config.has_token && (
              <button
                type="button"
                onClick={() => handleSave({ clear_token: true, enabled: false })}
                disabled={saving}
                className="flex items-center gap-1.5 border border-red-200 text-red-700 hover:bg-red-50 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
              >
                <Trash2 className="w-3.5 h-3.5" /> {t('settings.clearToken')}
              </button>
            )}
          </div>
          {testResult && (
            <div className={`mt-2 text-sm rounded-lg px-3 py-2 ${testResult.ok ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-red-50 border border-red-200 text-red-700'}`}>
              {testResult.ok
                ? <>{t('settings.connectedAs')} <strong>@{testResult.bot?.username}</strong> (id {testResult.bot?.id})</>
                : <>{t('settings.testFailed')}: {testResult.error}</>}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 pt-2 border-t border-gray-100">
          <div>
            <label className="text-sm font-medium text-gray-700">{t('settings.pollingEnabled')}</label>
            <p className="text-xs text-gray-500">{t('settings.whenOnTheBackendLong')}</p>
          </div>
          <label className="inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              className="sr-only peer"
              checked={config.enabled}
              disabled={saving || !config.has_token}
              onChange={(e) => handleSave({ enabled: e.target.checked })}
            />
            <span className="w-11 h-6 bg-gray-200 rounded-full peer peer-checked:bg-indigo-600 peer-disabled:opacity-50 relative transition-colors">
              <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${config.enabled ? 'translate-x-5' : ''}`} />
            </span>
          </label>
        </div>
      </SectionCard>

      <SectionCard title={t('settings.pollerStatus')}>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${status.running ? 'bg-green-500 animate-pulse' : 'bg-gray-400'}`} />
            <span className="text-gray-700">{status.running ? t('settings.running') : t('settings.stopped')}</span>
          </div>
          <div className="text-gray-700">
            {t('settings.telegram.bot')}: <strong>{status.bot_username ? `@${status.bot_username}` : '—'}</strong>
          </div>
          <div className="text-gray-700">
            {t('settings.telegram.lastPoll')}: <span className="text-gray-500">{status.last_poll ? new Date(status.last_poll).toLocaleString() : '—'}</span>
          </div>
          <div className="text-gray-700">
            {t('settings.telegram.lastError')}: <span className="text-red-600">{status.last_error || '—'}</span>
          </div>
        </div>
      </SectionCard>

      <SectionCard title={`${t('settings.telegram.chatBindings')} (${bindings.length})`}>
        <p className="text-sm text-gray-600">
          {t('settings.telegram.bindingsBefore')} <code className="text-xs bg-gray-100 rounded px-1">/agent &lt;id&gt;</code> {t('settings.telegram.bindingsAfter')}
        </p>
        {bindings.length === 0 ? (
          <div className="text-sm text-gray-500 flex items-center gap-2 py-3">
            <MessageSquare className="w-4 h-4" /> {t('settings.noBindingsYet')}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500 uppercase">
                  <th className="py-2 pr-3">{t('settings.chat')}</th>
                  <th className="py-2 pr-3">{t('settings.target')}</th>
                  <th className="py-2 pr-3">{t('settings.workspace')}</th>
                  <th className="py-2 pr-3">{t('settings.lastMessage')}</th>
                  <th className="py-2"></th>
                </tr>
              </thead>
              <tbody>
                {bindings.map((b) => (
                  <tr key={b.chat_id} className="border-t border-gray-100">
                    <td className="py-2 pr-3 font-mono text-xs text-gray-700">
                      {b.title || b.chat_id}
                      <div className="text-[10px] text-gray-400">{b.chat_id}</div>
                    </td>
                    <td className="py-2 pr-3 text-gray-800">
                      {b.flow_id ? (
                        <>
                          <span className="inline-flex items-center gap-1">
                            <span className="text-[10px] font-medium bg-indigo-100 text-indigo-700 border border-indigo-200 px-1.5 py-0.5 rounded">{t('settings.flow')}</span>
                            {b.flow_name || b.flow_id}
                          </span>
                          <div className="text-[10px] text-gray-400">{b.flow_id}</div>
                        </>
                      ) : b.agent_id ? (
                        <>
                          {b.agent_name || b.agent_id}
                          <div className="text-[10px] text-gray-400">{b.agent_id}</div>
                        </>
                      ) : (
                        <span className="text-gray-400 italic">{t('settings.notSet')}</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-gray-700">{b.workspace || '—'}</td>
                    <td className="py-2 pr-3 text-xs text-gray-500">
                      {b.last_message_at ? new Date(b.last_message_at).toLocaleString() : '—'}
                    </td>
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        onClick={() => handleDeleteBinding(b.chat_id)}
                        className="inline-flex items-center gap-1 text-xs text-red-600 hover:bg-red-50 border border-red-200 rounded-md px-2 py-1"
                      >
                        <Trash2 className="w-3 h-3" /> {t('settings.remove')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </div>
  );
}

