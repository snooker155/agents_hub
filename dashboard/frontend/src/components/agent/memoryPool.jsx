import { useState } from 'react';
import { ChevronDown, ChevronUp, Database, FileSearch, FileText, Hash, Layers, Loader, Zap } from 'lucide-react';
import { fmtBytes } from './formatBytes';
import { useI18n } from '../../i18n';

// ── Memory pool helpers ───────────────────────────────────────────────────────


const RAG_STATUS = {
  raw:     { labelKey: 'agentDetails.rag.raw',     color: 'bg-gray-100 text-gray-600',    dot: 'bg-gray-400' },
  indexed: { labelKey: 'agentDetails.rag.indexed', color: 'bg-green-100 text-green-700',  dot: 'bg-green-500' },
  failed:  { labelKey: 'agentDetails.rag.failed',  color: 'bg-red-100 text-red-700',      dot: 'bg-red-500' },
};

function RagBadge({ status, vectorized }) {
  const { t } = useI18n();
  const cfg = RAG_STATUS[status] || RAG_STATUS.raw;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.color}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      {vectorized ? t('agentDetails.rag.vectorized') : t(cfg.labelKey)}
    </span>
  );
}

function MemoryFileCard({ file, poolId }) {
  const { t } = useI18n();
  const [showPreview, setShowPreview] = useState(false);
  const [copied, setCopied] = useState(false);

  const ext = file.name.split('.').pop()?.toLowerCase() || '';
  const toolArgs = JSON.stringify({ memory_id: poolId, file_name: file.name }, null, 2);

  const copyTool = () => {
    navigator.clipboard.writeText(toolArgs).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="border border-gray-200 rounded-xl overflow-hidden bg-white">
      {/* Header */}
      <div className="px-4 py-3 flex items-start gap-3 border-b border-gray-100">
        <div className="p-2 bg-indigo-50 rounded-lg shrink-0">
          <FileText className="w-4 h-4 text-indigo-500" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="font-semibold text-gray-900 text-sm">{file.name}</p>
            {ext && <span className="text-xs bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded uppercase">{ext}</span>}
            <RagBadge status={file.rag_status || 'raw'} vectorized={file.vectorized} />
          </div>
          <div className="flex items-center gap-3 mt-1 text-xs text-gray-400">
            {file.size_bytes > 0 && <span>{fmtBytes(file.size_bytes)}</span>}
            {file.rag_chunks > 0 && <span>{t('agentDetails.chunkCount', { count: file.rag_chunks })}</span>}
            {file.embedding_dims > 0 && <span>{file.embedding_dims}d</span>}
          </div>
        </div>
        <button onClick={() => setShowPreview(v => !v)}
          className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-700 border border-gray-200 px-2 py-1 rounded-lg hover:bg-gray-50 shrink-0">
          <Eye className="w-3 h-3" /> {showPreview ? 'Hide' : 'Preview'}
        </button>
      </div>

      <div className="px-4 py-3 space-y-3">
        {/* read_memory tool call */}
        <div>
          <p className="text-xs font-medium text-gray-500 mb-1 flex items-center gap-1">
            <Hash className="w-3 h-3" /> {t('agentDetails.retrieval')} <code className="text-indigo-600">{t('agentDetails.readMemory')}</code> tool
          </p>
          <div className="bg-gray-900 rounded-lg px-3 py-2 flex items-start justify-between gap-2">
            <pre className="text-xs text-green-300 overflow-x-auto flex-1">{toolArgs}</pre>
            <button onClick={copyTool}
              className="text-gray-400 hover:text-white shrink-0 mt-0.5 transition-colors" title={t('agentDetails.copy')}>
              {copied ? <CheckCircle className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          </div>
        </div>

        {/* Vector metadata */}
        {file.vectorized && (
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs bg-indigo-50 rounded-lg px-3 py-2.5">
            <div className="flex items-center gap-1.5 col-span-2">
              <Zap className="w-3 h-3 text-indigo-500" />
              <span className="font-medium text-indigo-800">{t('agentDetails.vectorSearchAvailable')}</span>
            </div>
            {file.vector_db && <div><span className="text-gray-500">{t('agentDetails.vectorDb')}</span> <span className="font-medium text-gray-800">{file.vector_db}</span></div>}
            {file.vector_db_collection && <div><span className="text-gray-500">{t('agentDetails.collection')}</span> <span className="font-medium text-gray-800">{file.vector_db_collection}</span></div>}
            {file.embedding_model && <div><span className="text-gray-500">{t('agentDetails.model')}</span> <span className="font-medium text-gray-800">{file.embedding_model}</span></div>}
            {file.embedding_dims > 0 && <div><span className="text-gray-500">{t('agentDetails.dims')}</span> <span className="font-medium text-gray-800">{file.embedding_dims}</span></div>}
            {file.rag_chunk_size && <div><span className="text-gray-500">{t('agentDetails.chunkSize')}</span> <span className="font-medium text-gray-800">{t('agentDetails.charCount', { count: file.rag_chunk_size })}</span></div>}
          </div>
        )}

        {/* RAG only (no vector) */}
        {file.rag_status === 'indexed' && !file.vectorized && (
          <div className="text-xs bg-green-50 rounded-lg px-3 py-2 text-green-700 flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5" />
            {t('agentDetails.textChunkedInto', { chunks: file.rag_chunks, size: file.rag_chunk_size })}{' '}
            {t('agentDetails.configureVectorDbIn')} <strong>{t('agentDetails.settingsRagVectors')}</strong> {t('agentDetails.toEnableSemanticSearch')}
          </div>
        )}

        {/* Content preview */}
        {showPreview && (
          <div>
            <p className="text-xs font-medium text-gray-500 mb-1 flex items-center gap-1"><Eye className="w-3 h-3" /> {t('agentDetails.contentPreview')}</p>
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-xs text-gray-700 whitespace-pre-wrap max-h-48 overflow-y-auto">
              {file.content
                ? (file.content.length > 800 ? file.content.slice(0, 800) + '\n…' : file.content)
                : <span className="italic text-gray-400">{t('agentDetails.noContent')}</span>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function MemoryPoolDetails({ pool }) {
  const { t } = useI18n();
  const files = pool.files || [];
  const rawFiles      = files.filter(f => !f.rag_status || f.rag_status === 'raw');
  const indexedFiles  = files.filter(f => f.rag_status === 'indexed');
  const vectorized    = files.filter(f => f.vectorized);
  const totalChunks   = files.reduce((s, f) => s + (f.rag_chunks || 0), 0);

  const [filter, setFilter] = useState('all'); // all | raw | indexed | vectorized

  const visible = filter === 'all' ? files
    : filter === 'raw'       ? rawFiles
    : filter === 'indexed'   ? indexedFiles
    :                          vectorized;

  return (
    <div className="space-y-4">
      {/* Pool header */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <Link2 className="w-4 h-4 text-indigo-500 shrink-0" />
              <h3 className="font-bold text-gray-900">{pool.name}</h3>
              <span className="inline-flex items-center gap-1 text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-medium">
                <CheckCircle className="w-3 h-3" /> {t('agentDetails.connected')}
              </span>
            </div>
            {pool.description && <p className="text-sm text-gray-500 mt-1 ml-6">{pool.description}</p>}
            <p className="text-xs text-gray-400 mt-1 ml-6">{pool.id}</p>
          </div>
          <a href="/memory" className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-2 py-1 rounded-lg hover:bg-indigo-50 whitespace-nowrap flex items-center gap-1">
            <ExternalLink className="w-3 h-3" /> {t('agentDetails.manage')}
          </a>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-4 gap-3 mt-4">
          {[
            { label: t('agentDetails.stats.totalFiles'), value: files.length, icon: FileText, color: 'text-gray-600 bg-gray-50' },
            { label: t('agentDetails.stats.rawText'),    value: rawFiles.length, icon: FileSearch, color: 'text-gray-500 bg-gray-50' },
            { label: t('agentDetails.stats.indexed'),    value: indexedFiles.length, icon: Layers, color: 'text-green-700 bg-green-50' },
            { label: t('agentDetails.stats.vectorized'), value: vectorized.length, icon: Zap, color: 'text-indigo-700 bg-indigo-50' },
          ].map(({ label, value, icon: Icon, color }) => (
            <div key={label} className={`rounded-lg px-3 py-2.5 flex items-center gap-2.5 ${color}`}>
              <Icon className="w-4 h-4 shrink-0" />
              <div>
                <p className="text-lg font-bold leading-none">{value}</p>
                <p className="text-xs mt-0.5 opacity-70">{label}</p>
              </div>
            </div>
          ))}
        </div>

        {totalChunks > 0 && (
          <p className="text-xs text-gray-400 mt-3 flex items-center gap-1">
            <BarChart2 className="w-3 h-3" /> {totalChunks} total chunks across indexed files
          </p>
        )}
      </div>

      {/* File catalog */}
      {files.length === 0 ? (
        <div className="bg-white rounded-xl border border-dashed border-gray-200 p-10 text-center text-gray-400">
          <FileText className="w-10 h-10 mx-auto mb-3 opacity-20" />
          <p className="text-sm">{t('agentDetails.noFilesInThisPool')} <strong>{t('agentDetails.sharedMemory')}</strong> {t('agentDetails.page')}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {/* Filter bar */}
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold text-gray-700">{t('agentDetails.dataSources')}</p>
            <div className="flex gap-1 ml-auto">
              {[
                { id: 'all',        label: `All (${files.length})` },
                { id: 'raw',        label: `Raw (${rawFiles.length})` },
                { id: 'indexed',    label: `Indexed (${indexedFiles.length})` },
                { id: 'vectorized', label: `Vectorized (${vectorized.length})` },
              ].map(btn => (
                <button key={btn.id} onClick={() => setFilter(btn.id)}
                  className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
                    filter === btn.id ? 'bg-indigo-600 text-white' : 'bg-white border border-gray-200 text-gray-600 hover:bg-gray-50'
                  }`}>
                  {btn.label}
                </button>
              ))}
            </div>
          </div>

          {visible.length === 0 ? (
            <p className="text-center text-sm text-gray-400 py-6">{t('agentDetails.noFilesInThisCategory')}</p>
          ) : (
            <div className="space-y-3">
              {visible.map((f, i) => (
                <MemoryFileCard key={i} file={f} poolId={pool.id} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

// Tool ids the planning toggle owns; static data, kept out of the component so

export { RAG_STATUS, RagBadge, MemoryFileCard, MemoryPoolDetails };
