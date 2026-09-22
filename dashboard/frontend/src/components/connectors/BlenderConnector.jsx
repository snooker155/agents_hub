import { useCallback, useEffect, useState } from 'react';
import { Power, RefreshCw, Save, Wifi } from 'lucide-react';
import {
  getBlenderConfig, updateBlenderConfig, testBlenderBinary,
  getBlenderDaemons, stopBlenderDaemon, stopAllBlenderDaemons,
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


// ── Blender tab ──────────────────────────────────────────────────────────────
// Two things an operator needs from a geometry engine: whether one can run at
// all on this machine, and what is running right now. The daemon list is read
// from the cross-process registry, so it shows the engines agents started in
// their own processes, not only the ones the backend happens to have spawned.

function humanBytes(n) {
  if (!n) return '—';
  return `${Math.round(n / 1e6)} MB`;
}

function humanAge(seconds) {
  if (seconds == null) return '—';
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export default function BlenderConnector() {
  const { t } = useI18n();
  const [config, setConfig] = useState(null);
  const [daemons, setDaemons] = useState({ daemons: [], running: 0, max_daemons: 0 });
  const [pathInput, setPathInput] = useState('');
  const [probe, setProbe] = useState(null);
  const [testing, setTesting] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const loadConfig = useCallback(async () => {
    try {
      const { data } = await getBlenderConfig();
      setConfig(data);
      setPathInput(data.binary_path || '');
    } catch (e) {
      setError(`${t('settings.blender.loadFailed')}: ` + (e.response?.data?.detail || e.message));
    }
  }, [t]);

  const loadDaemons = useCallback(async () => {
    try {
      const { data } = await getBlenderDaemons();
      setDaemons(data);
    } catch { /* the engines list is a live view; a failed poll is not an error state */ }
  }, []);

  useEffect(() => { loadConfig(); }, [loadConfig]);
  // The daemon registry is cross-process, so an engine another process
  // started or stopped needs a live update rather than a fixed refresh point.
  // The backend publishes `blender_daemons.changed` on the app channel for
  // exactly this; the buttons on this card also reload it themselves, so an
  // action's own result is immediate either way.
  useEffect(() => { loadDaemons(); }, [loadDaemons]);
  useLiveRefetch(loadDaemons, { type: 'blender_daemons.changed' });

  const save = async (patch) => {
    setBusy('save');
    setError('');
    try {
      const { data } = await updateBlenderConfig(patch);
      setConfig(data);
      setPathInput(data.binary_path || '');
      setProbe(null);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy('');
    }
  };

  const test = async () => {
    setTesting(true);
    try {
      const { data } = await testBlenderBinary(pathInput.trim());
      setProbe(data);
    } catch (e) {
      setProbe({ ok: false, error: e.response?.data?.detail || e.message });
    } finally {
      setTesting(false);
    }
  };

  const stopOne = async (key) => {
    setBusy(key);
    try {
      await stopBlenderDaemon(key);
      await loadDaemons();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy('');
    }
  };

  const stopAll = async () => {
    setBusy('all');
    try {
      await stopAllBlenderDaemons();
      await loadDaemons();
    } finally {
      setBusy('');
    }
  };

  if (!config) {
    return (
      <div className="flex items-center justify-center py-10">
        <RefreshCw className="w-5 h-5 animate-spin text-indigo-500" />
      </div>
    );
  }

  const availability = config.availability || {};
  const numberField = (key, label, hint) => (
    <div>
      <label className="text-sm font-medium text-gray-700">{label}</label>
      <input
        type="number"
        value={config[key] ?? ''}
        onChange={(e) => setConfig({ ...config, [key]: e.target.value })}
        onBlur={(e) => {
          const value = parseInt(e.target.value, 10);
          if (Number.isFinite(value) && value !== 0) save({ [key]: value });
        }}
        className={inputCls}
      />
      <p className="text-xs text-gray-500 mt-1">{hint}</p>
    </div>
  );

  return (
    <div className="space-y-5">
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}

      <SectionCard title={t('settings.blender.title')}>
        <p className="text-sm text-gray-600">{t('settings.blender.intro')}</p>

        <div className="flex items-center gap-3">
          {availability.available ? (
            <span className="flex items-center gap-1.5 text-xs text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
              {availability.version || t('settings.available')}
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs text-red-700 bg-red-50 border border-red-200 px-2 py-0.5 rounded-full font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
              {availability.reason || t('settings.blender.unavailable')}
            </span>
          )}
          <label className="flex items-center gap-2 text-sm text-gray-700 ml-auto">
            <input
              type="checkbox"
              checked={!!config.enabled}
              onChange={(e) => save({ enabled: e.target.checked })}
            />
            {t('settings.blender.enabled')}
          </label>
        </div>

        <div>
          <label className="text-sm font-medium text-gray-700">{t('settings.blender.binaryPath')}</label>
          <div className="flex gap-2 mt-1">
            <input
              type="text"
              value={pathInput}
              onChange={(e) => setPathInput(e.target.value)}
              placeholder={config.discovered_binary || '/path/to/blender'}
              className={inputCls}
            />
            <button
              type="button"
              onClick={test}
              disabled={testing}
              className="flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50 whitespace-nowrap"
            >
              {testing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
              {t('settings.blender.test')}
            </button>
            <button
              type="button"
              onClick={() => save({ binary_path: pathInput.trim() })}
              disabled={busy === 'save'}
              className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />
              {t('common.save')}
            </button>
          </div>
          <p className="text-xs text-gray-500 mt-1">
            {config.discovered_binary
              ? t('settings.blender.discovered', { path: config.discovered_binary })
              : t('settings.blender.notDiscovered')}
          </p>
          {probe && (
            <p className={`text-xs mt-1 ${probe.ok ? 'text-green-700' : 'text-red-600'}`}>
              {probe.ok ? `${probe.version} · ${probe.path}` : probe.error}
            </p>
          )}
        </div>

        <div className="grid grid-cols-3 gap-4">
          {numberField('max_daemons', t('settings.blender.maxDaemons'), t('settings.blender.maxDaemonsHint'))}
          {numberField('idle_timeout_s', t('settings.blender.idleTimeout'), t('settings.blender.idleTimeoutHint'))}
          {numberField('command_timeout_s', t('settings.blender.commandTimeout'), t('settings.blender.commandTimeoutHint'))}
        </div>
      </SectionCard>

      <SectionCard title={t('settings.blender.engines')}>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-600">
            {t('settings.blender.engineCount', { running: daemons.running, max: daemons.max_daemons })}
          </span>
          {daemons.daemons?.length > 0 && (
            <button
              type="button"
              onClick={stopAll}
              disabled={busy === 'all'}
              className="ml-auto flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 text-gray-700 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
            >
              <Power className="w-3.5 h-3.5" />
              {t('settings.blender.stopAll')}
            </button>
          )}
        </div>

        {daemons.daemons?.length === 0 ? (
          <p className="text-sm text-gray-500">{t('settings.blender.noEngines')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-gray-400">
                  <th className="py-2 pr-4">{t('settings.blender.colScene')}</th>
                  <th className="py-2 pr-4">{t('settings.blender.colObjects')}</th>
                  <th className="py-2 pr-4">PID</th>
                  <th className="py-2 pr-4">{t('settings.blender.colCommands')}</th>
                  <th className="py-2 pr-4">{t('settings.blender.colUptime')}</th>
                  <th className="py-2 pr-4">{t('settings.blender.colMemory')}</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {daemons.daemons.map((d) => (
                  <tr key={d.key} className="border-t border-gray-100">
                    <td className="py-2 pr-4 font-mono text-xs text-gray-700">{d.key}</td>
                    <td className="py-2 pr-4 text-gray-600">
                      {d.busy ? <span className="text-amber-600">{t('settings.blender.working')}</span>
                        : ((d.objects || []).join(', ') || '—')}
                    </td>
                    <td className="py-2 pr-4 text-gray-500">{d.pid}</td>
                    <td className="py-2 pr-4 text-gray-500">{d.commands ?? '—'}</td>
                    <td className="py-2 pr-4 text-gray-500">{humanAge(d.uptime_s)}</td>
                    <td className="py-2 pr-4 text-gray-500">{humanBytes(d.rss_bytes)}</td>
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        onClick={() => stopOne(d.key)}
                        disabled={busy === d.key}
                        className="text-xs text-red-600 hover:text-red-700 font-medium disabled:opacity-50"
                      >
                        {t('settings.blender.stop')}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-gray-500">{t('settings.blender.enginesHint')}</p>
      </SectionCard>
    </div>
  );
}
