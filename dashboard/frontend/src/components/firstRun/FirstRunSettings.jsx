/**
 * Settings, First setup: when the first run was finished, and the one way to
 * see it again. Starting it again reloads the page, so the gate (FirstRunGate)
 * reads the new state and shows it in place of the app.
 */
import { useEffect, useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { SectionCard } from '../settingsUi';
import { useFormatters, useI18n } from '../../i18n';
import { firstRunAction, getFirstRun } from '../../api/firstRun';

export default function FirstRunSettings() {
  const { t } = useI18n();
  const { formatDate } = useFormatters();
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    getFirstRun().then(({ data }) => setStatus(data || {})).catch(() => setStatus({}));
  }, []);

  const restart = async () => {
    if (!window.confirm(t('firstRun.settings.confirm'))) return;
    setBusy(true);
    setError('');
    try {
      await firstRunAction('restart');
      window.location.reload();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || t('firstRun.errors.failed'));
      setBusy(false);
    }
  };

  return (
    <SectionCard title={t('firstRun.settings.title')}>
      <p className="text-sm text-gray-600">{t('firstRun.settings.intro')}</p>
      {status?.completed_at && (
        <p className="text-sm text-gray-500">{t('firstRun.settings.finished', { date: formatDate(status.completed_at) })}</p>
      )}
      {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
      <div>
        <button
          type="button"
          onClick={restart}
          disabled={busy || !status || status.applies === false}
          data-testid="first-run-restart"
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          <RotateCcw className="w-4 h-4" />
          {t('firstRun.settings.restart')}
        </button>
      </div>
    </SectionCard>
  );
}
