/**
 * Tab: Indexed Files — every RAG-indexed file across all pools, searchable
 * and filterable by pool.
 */
import { useState } from 'react';
import { Database, FileText, Files, CheckCircle, Search, BarChart2, FileSearch } from 'lucide-react';
import { useI18n } from '../../i18n';
import { StatusBadge } from './StatusBadge';
import { fmt } from './helpers';

function IndexedFilesTab({ memories }) {
  const { t } = useI18n();
  const [search, setSearch] = useState('');
  const [filterPool, setFilterPool] = useState('');

  // Flatten rag_files from all pools
  const ragFiles = memories.flatMap(m =>
    (m.rag_files || [])
      .filter(f => f.status === 'indexed')
      .map(f => ({ ...f, memory_name: m.name, memory_id: m.id }))
  );

  const pools = [...new Set(ragFiles.map(f => f.memory_name))];
  const filtered = ragFiles.filter(f => {
    const matchSearch = !search || f.filename.toLowerCase().includes(search.toLowerCase()) || f.memory_name.toLowerCase().includes(search.toLowerCase());
    const matchPool = !filterPool || f.memory_name === filterPool;
    return matchSearch && matchPool;
  });

  const totalChunks = filtered.reduce((sum, f) => sum + (f.chunks || 0), 0);

  return (
    <div className="space-y-5">
      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: t('memoryManager.stats.indexedFiles'), value: ragFiles.length, icon: FileSearch, color: 'text-indigo-600 bg-indigo-50' },
          { label: t('memoryManager.stats.totalChunks'), value: totalChunks, icon: BarChart2, color: 'text-green-600 bg-green-50' },
          { label: t('memoryManager.stats.showing'), value: filtered.length, icon: CheckCircle, color: 'text-teal-600 bg-teal-50' },
          { label: t('memoryManager.stats.memoryPools'), value: pools.length, icon: Database, color: 'text-purple-600 bg-purple-50' },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="bg-white rounded-xl border border-gray-200 p-4 flex items-center gap-4">
            <div className={`p-3 rounded-xl ${color}`}><Icon className="w-5 h-5" /></div>
            <div>
              <p className="text-2xl font-bold text-gray-900">{value}</p>
              <p className="text-xs text-gray-500">{label}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder={t('memoryManager.searchFiles')}
            className="w-full border border-gray-200 rounded-lg pl-9 pr-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
        </div>
        <select value={filterPool} onChange={e => setFilterPool(e.target.value)}
          className="border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none">
          <option value="">{t('memoryManager.allPools')}</option>
          {pools.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {filtered.length === 0 ? (
          <div className="p-12 text-center">
            <FileSearch className="w-12 h-12 text-gray-200 mx-auto mb-3" />
            <p className="text-gray-400 text-sm">
              {ragFiles.length === 0 ? t('memoryManager.noRagIndexedFiles') : t('memoryManager.noFilesMatchFilters')}
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-xs text-gray-500 uppercase tracking-wider bg-gray-50">
                <th className="px-5 py-3 text-left">{t('memoryManager.file')}</th>
                <th className="px-5 py-3 text-left">{t('memoryManager.pool')}</th>
                <th className="px-5 py-3 text-left">{t('memoryManager.status')}</th>
                <th className="px-5 py-3 text-left">{t('memoryManager.chunks')}</th>
                <th className="px-5 py-3 text-left">{t('memoryManager.indexedAt')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((f, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <FileText className="w-4 h-4 text-indigo-400 shrink-0" />
                      <span className="font-medium text-gray-900">{f.filename}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3">
                    <span className="bg-indigo-50 text-indigo-700 text-xs px-2 py-0.5 rounded-full font-medium">{f.memory_name}</span>
                  </td>
                  <td className="px-5 py-3"><StatusBadge status="indexed" /></td>
                  <td className="px-5 py-3 font-medium text-gray-800">{f.chunks || '—'}</td>
                  <td className="px-5 py-3 text-gray-400 text-xs">{fmt(f.indexed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export { IndexedFilesTab };
