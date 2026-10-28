import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, BookOpen, CheckCircle } from 'lucide-react';
import { getCatalogSettings, saveCatalogSettings } from '../../api/catalog';
import { SectionCard } from '../settingsUi';
import { MULTI, isAdmin, useAuth } from '../auth';
import { useI18n } from '../../i18n';

/**
 * Settings, hub link tab: use the linked hub only as a library (hublink/catalog.py).
 * The switch saves on its own (PUT /api/catalog/settings, admin only), since it
 * does not belong to the link form's save.
 */
export default function HubLinkCatalogCard() {
  const { t } = useI18n();
  const auth = useAuth();
  const canEdit = auth?.mode !== MULTI || isAdmin(auth);
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    try {
      const { data } = await getCatalogSettings();
      setState(data);
    } catch {
      setState({ linked: false, enabled: false });
    }
  }, []);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- the loader sets state after its await
  useEffect(() => { load(); }, [load]);

  const toggle = async (on) => {
    setBusy(true); setError(''); setNotice('');
    try {
      const { data } = await saveCatalogSettings(on);
      setState((s) => ({ ...s, enabled: !!data.enabled }));
      setNotice(t('catalog.settingsCard.saved'));
    } catch (e) {
      setError(e?.response?.data?.detail || t('catalog.settingsCard.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  if (!state) return null;
  return (
    <SectionCard title={t('catalog.settingsCard.title')}>
      <div data-testid="catalog-card" className="space-y-4">
        <p className="text-sm text-gray-600">{t('catalog.settingsCard.intro')}</p>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5" checked={!!state.enabled} disabled={busy || !canEdit || !state.linked}
            onChange={(e) => toggle(e.target.checked)} data-testid="catalog-toggle" />
          <span className="text-gray-800 font-medium">{t('catalog.settingsCard.toggle')}</span>
        </label>
        {!state.linked && <p className="text-xs text-gray-500">{t('catalog.settingsCard.notLinked')}</p>}
        {state.linked && !canEdit && <p className="text-xs text-gray-500">{t('catalog.settingsCard.adminOnly')}</p>}
        {error && <p className="flex items-center gap-1.5 text-sm text-red-600"><AlertCircle className="w-4 h-4 shrink-0" />{error}</p>}
        {notice && <p className="flex items-center gap-1.5 text-sm text-green-700"><CheckCircle className="w-4 h-4 shrink-0" />{notice}</p>}
        {state.enabled && (
          <Link to="/catalog" data-testid="catalog-open"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border border-gray-200 text-gray-700 hover:bg-gray-50">
            <BookOpen className="w-4 h-4" />{t('catalog.settingsCard.open')}
          </Link>
        )}
      </div>
    </SectionCard>
  );
}
