import { useCallback, useEffect, useMemo, useState } from 'react';
import { PanelLeft } from 'lucide-react';
import { getMemberMenu, setMemberMenu } from '../../api/memberMenu';
import { useI18n } from '../../i18n';
import { useAuth } from '../auth';
import { buildMenu } from '../navMenu';
import PageLoader from '../PageLoader';

// Never offered: the server drops them from the hidden list too.
const ALWAYS_SHOWN = ['/account', '/chat'];

const cardCls = 'bg-white border border-gray-200 rounded-xl overflow-hidden';
const sectionTitleCls = 'text-base font-semibold text-gray-900 flex items-center gap-2';

/**
 * The sidebar rows members do not see (Users page, administrators only).
 * The rows are the sidebar's own, built the way an administrator sees them,
 * so labels and grouping match; hiding one only tidies the menu.
 */
export default function MemberMenuSection() {
  const { t } = useI18n();
  const auth = useAuth();
  const [hidden, setHidden] = useState(new Set());
  const [isDefault, setIsDefault] = useState(true);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  const groups = useMemo(() => {
    const adminAuth = { ...auth, mode: 'multi', user: { ...(auth.user || {}), role: 'admin', hidden_menu: [] } };
    return buildMenu({ t, auth: adminAuth, features: auth.features || {} })
      .map((g) => ({ ...g, items: g.items.filter((it) => !ALWAYS_SHOWN.includes(it.path)) }))
      .filter((g) => g.items.length > 0);
  }, [t, auth]);

  const apply = (data) => {
    setHidden(new Set(data?.hidden || []));
    setIsDefault(Boolean(data?.is_default));
  };

  const load = useCallback(() => getMemberMenu()
    .then((r) => apply(r.data))
    .catch((err) => setError(err?.response?.data?.detail || t('access.loadFailed')))
    .finally(() => setLoading(false)), [t]);

  useEffect(() => { load(); }, [load]);

  const toggle = (path) => {
    setSaved(false);
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  };

  // `hidden: null` asks the server for its default.
  const send = async (value) => {
    setBusy(true);
    setError('');
    setSaved(false);
    try {
      const { data } = await setMemberMenu(value);
      apply(data);
      setSaved(true);
    } catch (err) {
      setError(err?.response?.data?.detail || t('access.saveFailed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-8" aria-labelledby="member-menu-title" data-testid="member-menu">
      <h2 id="member-menu-title" className={sectionTitleCls}>
        <PanelLeft className="w-4 h-4 text-indigo-600" /> {t('access.menuTitle')}
      </h2>
      <p className="text-xs text-gray-500 mt-1 mb-3">{t('access.menuDescription')}</p>

      {error && <p role="alert" className="mb-3 text-sm text-red-600">{error}</p>}

      <div className={`${cardCls} p-4`}>
        {loading ? (
          <PageLoader size="sm" />
        ) : (
          <>
            <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
              {groups.map((g) => (
                <div key={g.key}>
                  <p className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400">{g.label}</p>
                  {g.items.map((it) => (
                    <label key={it.path} className="flex items-center gap-2 py-1 text-sm text-gray-700">
                      <input type="checkbox" checked={hidden.has(it.path)}
                        aria-label={`${it.name}: ${t('access.hiddenFromMembers')}`}
                        data-path={it.path} onChange={() => toggle(it.path)} />
                      {it.name}
                    </label>
                  ))}
                </div>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button type="button" disabled={busy} onClick={() => send([...hidden])}
                className="bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-50">
                {t('access.save')}
              </button>
              <button type="button" disabled={busy} onClick={() => send(null)}
                className="text-sm text-gray-600 hover:text-gray-900 disabled:opacity-50">
                {t('access.resetDefault')}
              </button>
              <span className="text-xs text-gray-500">
                {saved ? t('access.saved') : isDefault ? t('access.usingDefault') : t('access.customized')}
              </span>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
