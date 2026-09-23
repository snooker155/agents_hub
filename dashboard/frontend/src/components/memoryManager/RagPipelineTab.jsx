/**
 * Tab: RAG Pipeline — vector DB status, file upload and indexing per pool.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Database, Trash2, Upload, Files, RefreshCw, CheckCircle, AlertCircle, Zap,
} from 'lucide-react';
import {
  uploadMemoryFile, deleteMemoryFile, indexMemoryFile, deindexMemoryFile,
  listMemoryFiles, getRagConfig,
} from '../../api';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';
import { StatusBadge } from './StatusBadge';
import { fmt } from './helpers';

const PROVIDER_LABELS = {
  none: null, openai: 'OpenAI', 'sentence-transformers': 'Sentence-Transformers',
  ollama: 'Ollama', google: 'Google',
};
const DB_LABELS = {
  none: null, chroma: 'ChromaDB', pinecone: 'Pinecone', qdrant: 'Qdrant',
};

function VectorDbStatusCard({ ragCfg }) {
  const { t } = useI18n();
  if (!ragCfg) return null;
  const active = ragCfg.is_configured;
  return (
    <div className={`rounded-xl border p-4 flex items-start gap-4 ${active ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200'}`}>
      <div className={`p-2.5 rounded-xl shrink-0 ${active ? 'bg-green-100' : 'bg-gray-200'}`}>
        <Database className={`w-5 h-5 ${active ? 'text-green-700' : 'text-gray-500'}`} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="font-semibold text-sm text-gray-800">{t('memoryManager.vectorDbStatus')}</p>
          {active
            ? <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-100 text-green-700 text-xs rounded-full font-medium"><CheckCircle className="w-3 h-3" /> {t('memoryManager.active')}</span>
            : <span className="text-xs bg-gray-200 text-gray-600 px-2 py-0.5 rounded-full">{t('memoryManager.notConfigured')}</span>}
        </div>
        <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-gray-600">
          <span><span className="text-gray-400">{t('memoryManager.vectorDb')}</span> {DB_LABELS[ragCfg.vector_db] || (ragCfg.vector_db === 'none' ? t('memoryManager.none') : ragCfg.vector_db)}</span>
          <span><span className="text-gray-400">{t('memoryManager.embedding')}</span> {PROVIDER_LABELS[ragCfg.embedding_provider] || (ragCfg.embedding_provider === 'none' ? t('memoryManager.notConfigured') : ragCfg.embedding_provider)}</span>
          {ragCfg.vector_db !== 'none' && ragCfg.vector_db_collection && (
            <span><span className="text-gray-400">{t('memoryManager.collection')}</span> {ragCfg.vector_db_collection}</span>
          )}
          {ragCfg.embedding_provider !== 'none' && ragCfg.embedding_model && (
            <span><span className="text-gray-400">{t('memoryManager.model')}</span> <code className="bg-white rounded px-1">{ragCfg.embedding_model}</code></span>
          )}
        </div>
        {!active && (
          <p className="text-xs text-gray-500 mt-2">
            {t('memoryManager.configureVectorBefore')} <strong>{t('memoryManager.settingsRagVectors')}</strong> {t('memoryManager.configureVectorAfter')}
          </p>
        )}
      </div>
    </div>
  );
}

function RagPipelineTab({ memories, workspaceFilter }) {
  const { t } = useI18n();
  const toast = useToast();
  const [poolId, setPoolId] = useState(memories[0]?.id || '');
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [indexing, setIndexing] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [ragCfg, setRagCfg] = useState(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef(null);

  useEffect(() => {
    getRagConfig().then(r => setRagCfg(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!poolId && memories.length > 0) setPoolId(memories[0].id);
  }, [memories, poolId]);

  const loadFiles = useCallback(async (id, workspace) => {
    if (!id || !workspace) return;
    setLoading(true);
    try {
      const resp = await listMemoryFiles(id, workspace);
      setFiles(resp.data.files || []);
    } catch { setFiles([]); } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (poolId && workspaceFilter) loadFiles(poolId, workspaceFilter);
    else setFiles([]);
  }, [poolId, workspaceFilter, loadFiles]);

  const handleUploadFiles = async (fileList) => {
    if (!poolId || !workspaceFilter) return;
    setUploading(true);
    try {
      for (const file of fileList) {
        await uploadMemoryFile(poolId, workspaceFilter, file);
      }
      await loadFiles(poolId, workspaceFilter);
    } catch (e) {
      toast.error(t('memoryManager.errors.uploadFile'), errorDetail(e));
    } finally { setUploading(false); }
  };

  const handleDrop = async (e) => {
    e.preventDefault();
    setDragging(false);
    await handleUploadFiles([...e.dataTransfer.files]);
  };

  const handleIndex = async (filename) => {
    setIndexing(filename);
    try {
      await indexMemoryFile(poolId, filename, workspaceFilter);
      await loadFiles(poolId, workspaceFilter);
    } catch (e) {
      toast.error(t('memoryManager.errors.indexFile'), errorDetail(e));
    } finally { setIndexing(null); }
  };

  const handleDeindex = async (filename) => {
    setIndexing(filename);
    try {
      await deindexMemoryFile(poolId, filename);
      await loadFiles(poolId, workspaceFilter);
    } catch (e) {
      toast.error(t('memoryManager.errors.deindexFile'), errorDetail(e));
    } finally { setIndexing(null); }
  };

  const handleDelete = async (filename) => {
    if (!window.confirm(`Delete "${filename}"?`)) return;
    try {
      await deleteMemoryFile(poolId, filename, workspaceFilter);
      await loadFiles(poolId, workspaceFilter);
    } catch (e) {
      toast.error(t('memoryManager.errors.deleteFile'), errorDetail(e));
    }
  };

  const handleIndexAll = async () => {
    const pending = files.filter(f => f.status !== 'indexed');
    for (const f of pending) await handleIndex(f.filename);
  };

  const indexedCount = files.filter(f => f.status === 'indexed').length;
  const pendingCount = files.length - indexedCount;
  const totalChunks = files.reduce((s, f) => s + (f.chunks || 0), 0);
  const showVectorCols = ragCfg?.is_configured;

  return (
    <div className="space-y-5">
      {/* Vector DB status */}
      <VectorDbStatusCard ragCfg={ragCfg} />

      {/* Pool selector */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex-1 min-w-48">
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('memoryManager.targetMemoryPool')}</label>
            <select value={poolId} onChange={e => setPoolId(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none">
              {memories.length === 0 ? (
                <option value="">{t('memoryManager.noPoolsCreateOneFirst')}</option>
              ) : (
                memories.map(m => <option key={m.id} value={m.id}>{m.name}</option>)
              )}
            </select>
          </div>
          {pendingCount > 0 && (
            <button onClick={handleIndexAll}
              className="flex items-center gap-2 bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 text-sm font-medium">
              <Zap className="w-4 h-4" /> Index All Pending ({pendingCount})
            </button>
          )}
          <button onClick={() => loadFiles(poolId, workspaceFilter)} disabled={loading}
            className="flex items-center gap-1 border border-gray-200 text-gray-500 px-3 py-2 rounded-lg hover:bg-gray-50 text-sm">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-indigo-400' : ''}`} />
          </button>
        </div>
      </div>

      {/* Stats bar */}
      {files.length > 0 && (
        <div className={`grid gap-4 ${showVectorCols ? 'grid-cols-4' : 'grid-cols-3'}`}>
          {[
            { label: t('memoryManager.stats.totalFiles'), value: files.length, color: 'bg-gray-50 text-gray-600' },
            { label: t('memoryManager.stats.pending'), value: pendingCount, color: 'bg-yellow-50 text-yellow-700' },
            { label: t('memoryManager.stats.indexed'), value: indexedCount, color: 'bg-green-50 text-green-700' },
            ...(showVectorCols ? [{ label: t('memoryManager.stats.totalChunks'), value: totalChunks, color: 'bg-indigo-50 text-indigo-700' }] : []),
          ].map(({ label, value, color }) => (
            <div key={label} className={`rounded-xl border border-gray-200 p-4 text-center ${color}`}>
              <p className="text-2xl font-bold">{value}</p>
              <p className="text-xs font-medium mt-0.5">{label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Upload zone */}
      {workspaceFilter ? (
        <div
          onDragOver={e => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          className={`border-2 border-dashed rounded-xl p-10 text-center transition-colors cursor-pointer ${
            dragging ? 'border-indigo-400 bg-indigo-50' : 'border-gray-200 bg-white hover:border-indigo-300 hover:bg-gray-50'
          }`}
          onClick={() => fileInputRef.current?.click()}
        >
          <input ref={fileInputRef} type="file" multiple className="hidden"
            accept=".txt,.md,.json,.yaml,.yml,.csv,.xml,.rst,.log"
            onChange={e => { handleUploadFiles([...e.target.files]); e.target.value = ''; }} />
          {uploading
            ? <RefreshCw className="w-10 h-10 mx-auto mb-3 animate-spin text-indigo-400" />
            : <Upload className={`w-10 h-10 mx-auto mb-3 ${dragging ? 'text-indigo-500' : 'text-gray-300'}`} />
          }
          <p className="text-gray-600 font-medium">{uploading ? t('memoryManager.uploading') : t('memoryManager.dropFilesHere')}</p>
          <p className="text-xs text-gray-400 mt-1">{t('memoryManager.supportsTxtMdJsonYaml')}</p>
        </div>
      ) : (
        <div className="border-2 border-dashed rounded-xl p-10 text-center border-gray-200 bg-gray-50">
          <AlertCircle className="w-10 h-10 mx-auto mb-3 text-gray-300" />
          <p className="text-gray-400 text-sm">{t('memoryManager.selectAWorkspaceToUpload')}</p>
        </div>
      )}

      {/* File list */}
      {poolId && files.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-5 py-3 bg-gray-50 border-b border-gray-100">
            <h3 className="font-semibold text-gray-700 text-sm flex items-center gap-2">
              <Files className="w-4 h-4 text-indigo-500" /> {t('memoryManager.filesInPool', { pool: memories.find(m => m.id === poolId)?.name || poolId })}
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs text-gray-500 uppercase tracking-wider">
                  <th className="px-5 py-2 text-left">{t('memoryManager.file')}</th>
                  <th className="px-5 py-2 text-left">{t('memoryManager.status')}</th>
                  <th className="px-5 py-2 text-left">{t('memoryManager.chunks')}</th>
                  <th className="px-5 py-2 text-left">{t('memoryManager.indexedAt')}</th>
                  <th className="px-5 py-2 text-right">{t('memoryManager.action')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {files.map((f) => {
                  const isIndexed = f.status === 'indexed';
                  const isBusy = indexing === f.filename;
                  return (
                    <tr key={f.filename} className={`hover:bg-gray-50 ${isBusy ? 'bg-indigo-50/40' : ''}`}>
                      <td className="px-5 py-3">
                        <p className="font-medium text-gray-800">{f.filename}</p>
                      </td>
                      <td className="px-5 py-3">
                        {isBusy
                          ? <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-100 text-indigo-700 text-xs rounded-full font-medium animate-pulse"><RefreshCw className="w-3 h-3 animate-spin" /> {t('memoryManager.working')}</span>
                          : <StatusBadge status={isIndexed ? 'indexed' : 'raw'} />
                        }
                      </td>
                      <td className="px-5 py-3 text-gray-500 text-xs">{isIndexed ? (f.chunks || '—') : '—'}</td>
                      <td className="px-5 py-3 text-gray-400 text-xs">{fmt(f.indexed_at)}</td>
                      <td className="px-5 py-3 text-right">
                        <div className="flex items-center gap-1 justify-end">
                          {isBusy ? (
                            <RefreshCw className="w-3.5 h-3.5 animate-spin text-indigo-400" />
                          ) : isIndexed ? (
                            <button onClick={() => handleDeindex(f.filename)}
                              className="flex items-center gap-1 text-xs border border-gray-200 text-gray-500 px-2 py-1 rounded-lg hover:bg-gray-50">
                              {t('memoryManager.deIndex')}
                            </button>
                          ) : (
                            <button onClick={() => handleIndex(f.filename)}
                              className="flex items-center gap-1 text-xs bg-indigo-600 text-white px-2 py-1 rounded-lg hover:bg-indigo-700">
                              <Zap className="w-3 h-3" /> {t('memoryManager.index')}
                            </button>
                          )}
                          <button onClick={() => handleDelete(f.filename)} className="text-gray-300 hover:text-red-500 p-0.5 ml-1">
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export { VectorDbStatusCard, RagPipelineTab };
