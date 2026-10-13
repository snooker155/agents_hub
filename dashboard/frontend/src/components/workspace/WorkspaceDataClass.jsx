import { useEffect, useState } from 'react';
import { Loader } from 'lucide-react';
import { getWorkspaceDataClass, setWorkspaceDataClass } from '../../api/isolation';
import { SectionCard } from '../settingsUi';
import { errorDetail } from '../toast';
import { useI18n } from '../../i18n';

const CLASSES = ['public', 'internal', 'confidential'];
const selectCls = 'border border-gray-200 rounded-lg px-2 py-1.5 text-sm focus:outline-none';

/**
 * How sensitive a workspace's work is (common/data_class.py): decides which
 * linked nodes may see it. Owner or admin only, so a failed save shows the
 * server's reason. Saves on change, like the other per workspace cards.
 */
export default function WorkspaceDataClass({ workspace }) {
  const { t } = useI18n();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getWorkspaceDataClass(workspace)
      .then(({ data: body }) => { if (!cancelled) setData(body); })
      .catch((e) => { if (!cancelled) setError(errorDetail(e) || t('dataClass.errors.load')); });
    return () => { cancelled = true; };
  // `t` changes with the language only; the class does not need a refetch.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace]);

  const change = async (next) => {
    setSaving(true);
    setSaved(false);
    setError('');
    try {
      const { data: body } = await setWorkspaceDataClass(workspace, next);
      setData(body);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(errorDetail(e) || t('dataClass.errors.save'));
    } finally {
      setSaving(false);
    }
  };

  if (!data) {
    return (
      <SectionCard title={t('dataClass.title')}>
        {error
          ? <p className="text-sm text-red-600">{error}</p>
          : <p className="text-sm text-gray-500 flex items-center gap-2"><Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}</p>}
      </SectionCard>
    );
  }

  const current = data.data_class;
  const need = data.required_trust?.[current];

  return (
    <SectionCard title={t('dataClass.title')}>
      <p className="text-sm text-gray-600">{t('dataClass.explainer')}</p>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="ws-data-class">{t('dataClass.label')}</label>
        <div className="flex items-center gap-3">
          <select
            id="ws-data-class"
            value={current}
            disabled={saving}
            onChange={(e) => change(e.target.value)}
            className={selectCls}
            data-testid="workspace-data-class"
          >
            {(data.classes || CLASSES).map((c) => (
              <option key={c} value={c}>{t(`dataClass.${c}`)}</option>
            ))}
          </select>
          {saving && <Loader className="w-4 h-4 animate-spin text-gray-400" />}
          {saved && <span className="text-xs text-green-700">{t('dataClass.saved')}</span>}
        </div>
        <p className="text-xs text-gray-500 mt-2" data-testid="data-class-description">{t(`dataClass.desc.${current}`)}</p>
        {need && <p className="text-xs text-gray-400 mt-1">{t('dataClass.needs', { trust: t(`nodes.trust.${need}`) })}</p>}
      </div>
      <ul className="text-xs text-gray-500 space-y-1">
        {CLASSES.map((c) => (
          <li key={c}><span className="font-medium text-gray-700">{t(`dataClass.${c}`)}</span>{': '}{t(`dataClass.desc.${c}`)}</li>
        ))}
      </ul>
      {error && (
        <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2" data-testid="data-class-error">{error}</p>
      )}
    </SectionCard>
  );
}
