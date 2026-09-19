import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Images, RefreshCw, Trash2, Box, Search, Boxes } from 'lucide-react';
import { listViews, deleteView } from '../api';
import { useWorkspace } from '../components/workspace';
import ViewCard from '../views/ViewCard';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { useToast, errorDetail } from '../components/toast';
// Views gallery — every rich view an agent produced, searchable and filterable
// by kind, each rendered live. Live-buildable kinds link back into the Studio.

// Kinds that open in the Studio (built via the op protocol / live runtimes).
const STUDIO_KINDS = new Set(['graph', 'scene3d', 'simulation', 'math', 'process', 'chart', 'table', 'html', 'diagram', 'latex', 'slides', 'document']);

export default function Views() {
  const { t } = useI18n();
  const toast = useToast();
  const { selectedWorkspace } = useWorkspace();
  const navigate = useNavigate();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('all');

  // No setLoading(true) here: `loading` starts true for the first load, and on a
  // workspace switch the current list stays up until the new one lands.
  const load = useCallback(() => {
    const params = selectedWorkspace ? { workspace: selectedWorkspace } : {};
    listViews(params)
      .then((r) => { setRows(r.data?.views || []); setError(null); })
      .catch((e) => setError(e?.response?.data?.detail || t('views.loadFailed')))
      .finally(() => setLoading(false));
  }, [selectedWorkspace, t]);

  useEffect(() => { load(); }, [load]);

  const onDelete = async (viewId) => {
    if (!window.confirm(t('views.confirmDelete'))) return;
    try {
      await deleteView(viewId);
      setRows((rs) => rs.filter((v) => v.view_id !== viewId));
    } catch (e) {
      toast.error(t('views.deleteFailed'), errorDetail(e));
    }
  };

  const kinds = useMemo(() => ['all', ...Array.from(new Set(rows.map((r) => r.kind))).sort()], [rows]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (kind !== 'all' && r.kind !== kind) return false;
      if (!q) return true;
      return `${r.title} ${r.summary} ${r.kind}`.toLowerCase().includes(q);
    });
  }, [rows, query, kind]);

  return (
    <PageContainer>
      <PageHeader
        icon={Images}
        title={t('views.views')}
        description={selectedWorkspace ? `Visualizations produced by agents in ${selectedWorkspace}` : t('views.description')}
        actions={<>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t('views.searchViews')}
              className="pl-8 pr-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 w-48" />
          </div>
          <select value={kind} onChange={(e) => setKind(e.target.value)}
            className="py-1.5 px-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
            {kinds.map((k) => <option key={k} value={k}>{k === 'all' ? t('views.allKinds') : k}</option>)}
          </select>
          <button onClick={() => navigate('/studio')}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-indigo-600 text-white hover:bg-indigo-700">
            <Boxes className="w-4 h-4" /> {t('views.studio')}
          </button>
          <button onClick={load}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            <RefreshCw className="w-4 h-4" />
          </button>
        </>}
      />

      {loading && <div className="text-gray-400 py-12 text-center">{t('views.loading')}</div>}
      {error && <div className="text-red-600 py-4">{error}</div>}

      {!loading && !error && filtered.length === 0 && (
        <div className="text-center py-20 text-gray-400">
          <Box className="w-10 h-10 mx-auto mb-3 opacity-40" />
          <p>{rows.length ? t('views.noViewsMatch') : t('views.noViewsYet')}</p>
          {!rows.length && <p className="text-sm mt-1">{t('views.emptyHint')}</p>}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4">
        {filtered.map((row) => (
          <ViewCard
            key={row.view_id}
            compact
            viewRef={{ view_id: row.view_id, view_kind: row.kind, title: row.title, summary: row.summary }}
            actions={(
              <>
                {STUDIO_KINDS.has(row.kind) && (
                  <button onClick={() => navigate(`/studio/${row.view_id}`)} title={t('views.openInStudio')}
                    className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 hover:text-indigo-600">
                    <Boxes className="w-4 h-4" />
                  </button>
                )}
                <button onClick={() => onDelete(row.view_id)} title={t('views.deleteView')}
                  className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 hover:text-red-600">
                  <Trash2 className="w-4 h-4" />
                </button>
              </>
            )}
          />
        ))}
      </div>
    </PageContainer>
  );
}
