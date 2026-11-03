import { useEffect, useState } from 'react';
import { FolderX, FolderPlus, LogOut } from 'lucide-react';
import { createPersonalWorkspace, getMe } from '../api';
import { useAuth } from './auth';
import { useI18n } from '../i18n';

/**
 * In place of the whole app when a signed-in person reaches no workspace at
 * all: nothing in the app works without one. Usually their personal workspace
 * was deleted (by them or an administrator) and they belong to no other; a new
 * one is made at their next sign-in, or here and now with the button
 * (POST /api/auth/personal-workspace, common/personal_workspace.py).
 */
export default function NoWorkspaceScreen({ onCreated }) {
  const auth = useAuth();
  const { t } = useI18n();
  const [deleted, setDeleted] = useState(Boolean(auth.user?.personal_workspace_deleted));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // The user record from the sign-in does not say whether the personal
  // workspace was deleted since; /api/auth/me does.
  useEffect(() => {
    let cancelled = false;
    getMe()
      .then(({ data }) => { if (!cancelled) setDeleted(Boolean(data?.personal_workspace_deleted)); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const create = async () => {
    setBusy(true);
    setError('');
    try {
      const { data } = await createPersonalWorkspace();
      await onCreated?.(data?.name);
    } catch (e) {
      setError(e?.response?.data?.detail || t('layout.noWorkspace.failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app-shell min-h-screen w-full flex items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-md bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
        <div className="flex items-center gap-2">
          <span className="h-9 w-9 rounded-lg bg-amber-50 flex items-center justify-center text-amber-600">
            <FolderX className="w-5 h-5" />
          </span>
          <h1 className="text-base font-semibold text-gray-900">{t('layout.noWorkspace.title')}</h1>
        </div>
        <p className="text-sm text-gray-600">{t('layout.noWorkspace.body')}</p>
        {deleted && <p className="text-sm text-gray-600">{t('layout.noWorkspace.deleted')}</p>}
        {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
        <div className="flex items-center justify-between gap-3 pt-2">
          <button
            type="button"
            onClick={() => auth.logout()}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 text-sm"
          >
            <LogOut className="w-4 h-4" />
            {t('auth.logout')}
          </button>
          <button
            type="button"
            onClick={create}
            disabled={busy}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-60 text-sm font-medium"
          >
            <FolderPlus className="w-4 h-4" />
            {t('layout.noWorkspace.create')}
          </button>
        </div>
      </div>
    </div>
  );
}
