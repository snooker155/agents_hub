/**
 * Docs, Welcome: a section of its own under the changelog, the way to see the
 * welcome again (components/firstRun/Welcome.jsx, common/member_welcome.py).
 * Only in multi mode (sections.jsx `multiOnly`): a member does not see
 * Settings, so it lives here, where everyone does. Starting it again reloads
 * the page, so the gate shows it in place of the app.
 */
import { useEffect, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { isMultiUser, useAuth } from '../auth';
import { useI18n } from '../../i18n';
import { getWelcome, welcomeAction } from '../../api/firstRun';
import { H2, P } from './DocsPrimitives';

export default function WelcomeDoc() {
  const { t } = useI18n();
  const multi = isMultiUser(useAuth());
  const [applies, setApplies] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!multi) return undefined;
    let live = true;
    getWelcome().then(({ data }) => { if (live) setApplies(Boolean(data?.applies)); }).catch(() => {});
    return () => { live = false; };
  }, [multi]);

  const replay = async () => {
    if (!window.confirm(t('firstRun.welcome.replay.confirm'))) return;
    setBusy(true);
    setError('');
    try {
      await welcomeAction('restart');
      window.location.reload();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || t('firstRun.errors.failed'));
      setBusy(false);
    }
  };

  return (
    <div>
      <H2>{t('docs.nav.welcome')}</H2>
      <P>{t('firstRun.welcome.replay.intro')}</P>
      {multi && applies && (
        <div className="my-4 flex flex-wrap items-center gap-3 rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-3"
          data-testid="replay-welcome">
          <button
            type="button"
            onClick={replay}
            disabled={busy}
            className="flex items-center gap-1.5 shrink-0 text-sm font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition-colors disabled:opacity-50"
          >
            <Sparkles className="w-4 h-4" />
            {t('firstRun.welcome.replay.button')}
          </button>
          <span className="text-sm text-gray-600">{t('firstRun.welcome.replay.hint')}</span>
          {error && <p className="w-full text-sm text-red-600" role="alert">{error}</p>}
        </div>
      )}
    </div>
  );
}
