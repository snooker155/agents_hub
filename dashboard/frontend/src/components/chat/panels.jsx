/**
 * The side panel's two halves: what the agent's process looked like, and the
 * files it changed, with the diff for each.
 */
import GraphMirror from '../GraphMirror';
import ProcessGraph, { TokenPill } from '../ProcessGraph';
import ViewCard from '../../views/ViewCard';
import { useI18n } from '../../i18n';
import { ChevronDown, ChevronUp, FileText, LayoutGrid, Loader2, Workflow } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { getWorkspaceFileContent } from '../../api';
import CodeBlock from '../CodeBlock';
import { codeLanguageFor } from '../../lib/codeLanguage';

function ProcessPanelContent({ processInsights, topology = null, graphRun = null, workspace = null }) {
  const { t } = useI18n();
  const graphKey = (processInsights?.message_runs || [])
    .map((mr, idx) => `${mr?.run_id || mr?.message_id || idx}`)
    .join('|');
  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-3">
      <div className="flex flex-wrap gap-1">
        <TokenPill label={t('chat.sessionIn')} value={processInsights?.token_usage?.inbound_tokens || 0} />
        <TokenPill label={t('chat.sessionOut')} value={processInsights?.token_usage?.outbound_tokens || 0} />
        <TokenPill label={t('chat.sessionTotal')} value={processInsights?.token_usage?.total_tokens || 0} />
        {/* The bill above sums every LLM call of every turn — an agent loop
            resends the conversation each step. This is the largest single
            prompt the session sent, the same figure the context meter tracks. */}
        {(processInsights?.context_peak || 0) > 0 && (
          <TokenPill
            label={t('chat.sessionPeak')}
            value={
              processInsights.context_window_tokens > 0
                ? `${processInsights.context_peak} / ${processInsights.context_window_tokens}`
                : processInsights.context_peak
            }
            title={t('chat.sessionPeakTooltip')}
          />
        )}
      </div>
      {/* An imported agent that is a graph inside: its own shape, with the node
          it is in right now lit and the ones it has been through marked. The
          hub does not run this graph, so there is nothing to click — the value
          is seeing which branch a live run took. */}
      {topology?.nodes?.length > 0 && (
        <div className="border border-gray-100 rounded-lg p-3">
          <div className="flex items-center gap-1.5 mb-2">
            <Workflow className="w-3.5 h-3.5 text-indigo-500" />
            <span className="text-xs font-semibold text-gray-700">{t('chat.agentGraph')}</span>
            {graphRun?.active && (
              <span className="text-[11px] text-indigo-600 font-medium truncate">{graphRun.active}</span>
            )}
          </div>
          <GraphMirror
            topology={topology}
            activeNode={graphRun?.active || null}
            visitedNodes={graphRun?.visited || []}
          />
        </div>
      )}
      <ProcessGraph
        key={graphKey}
        messageRuns={processInsights.message_runs || []}
        evalCaseWorkspace={workspace}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Build view — unified diff renderer (no external deps)
// ---------------------------------------------------------------------------
function DiffView({ diff }) {
  const { t } = useI18n();
  if (!diff) {
    return <div className="px-3 py-2 text-xs text-gray-400 italic">{t('chat.noTextualDiffAvailable')}</div>;
  }
  const lines = diff.split('\n');
  return (
    <div className="font-mono text-[13px] leading-relaxed overflow-x-auto">
      {lines.map((line, i) => {
        let cls = 'text-gray-600';
        let bg = '';
        if (line.startsWith('+++') || line.startsWith('---')) {
          cls = 'text-gray-400';
        } else if (line.startsWith('@@')) {
          cls = 'text-indigo-500';
          bg = 'bg-indigo-50/60';
        } else if (line.startsWith('+')) {
          cls = 'text-emerald-700';
          bg = 'bg-emerald-50';
        } else if (line.startsWith('-')) {
          cls = 'text-red-700';
          bg = 'bg-red-50';
        }
        return (
          <div key={i} className={`px-3 whitespace-pre ${bg} ${cls}`}>
            {line || ' '}
          </div>
        );
      })}
    </div>
  );
}

const ARTIFACT_OP_META = {
  add: { label: 'A', cls: 'bg-emerald-100 text-emerald-700' },
  modify: { label: 'M', cls: 'bg-amber-100 text-amber-700' },
  delete: { label: 'D', cls: 'bg-red-100 text-red-700' },
};

function ArtifactItem({ artifact, defaultOpen = false }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const meta = ARTIFACT_OP_META[artifact.op] || ARTIFACT_OP_META.modify;
  return (
    <div data-artifact-path={artifact.path} className="rounded-lg border border-gray-200 bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-50"
      >
        <span className={`w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold flex-shrink-0 ${meta.cls}`}>
          {meta.label}
        </span>
        <span className="flex-1 min-w-0 text-xs font-medium text-gray-700 truncate" title={artifact.path}>
          {artifact.path}
        </span>
        <span className="flex items-center gap-1.5 flex-shrink-0 text-[10px] font-mono">
          {artifact.additions > 0 && <span className="text-emerald-600">+{artifact.additions}</span>}
          {artifact.deletions > 0 && <span className="text-red-600">−{artifact.deletions}</span>}
        </span>
        {open ? <ChevronUp className="w-3 h-3 text-gray-400" /> : <ChevronDown className="w-3 h-3 text-gray-400" />}
      </button>
      {open && (
        <div className="border-t border-gray-100 py-2 max-h-[400px] overflow-y-auto">
          {artifact.binary ? (
            <div className="px-3 py-2 text-xs text-gray-400 italic">
              {artifact.truncated ? t('chat.fileTooLarge') : t('chat.binaryFile')}
            </div>
          ) : (
            <DiffView diff={artifact.diff} />
          )}
        </div>
      )}
    </div>
  );
}

/** The file as it is now in the workspace folder, read when asked for. */
function ArtifactContent({ workspace, path }) {
  const { t } = useI18n();
  // Mounted per file (keyed by path above), so the fetch runs once per file.
  const [state, setState] = useState({ loading: Boolean(workspace) });
  useEffect(() => {
    if (!workspace) return undefined;
    let cancelled = false;
    getWorkspaceFileContent(workspace, path)
      .then((r) => { if (!cancelled) setState({ content: r.data?.content ?? '' }); })
      .catch((e) => {
        if (!cancelled) setState({ error: e?.response?.data?.detail || t('chat.artifactContentFailed') });
      });
    return () => { cancelled = true; };
  }, [workspace, path, t]);
  if (!workspace) return <div className="p-3 text-xs text-red-600">{t('chat.artifactNoWorkspace')}</div>;
  if (state.loading) {
    return (
      <div className="flex items-center gap-2 p-3 text-xs text-gray-500">
        <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('chat.artifactLoading')}
      </div>
    );
  }
  if (state.error) return <div className="p-3 text-xs text-red-600">{state.error}</div>;
  const language = codeLanguageFor(path);
  if (language) return <div className="p-3"><CodeBlock language={language} code={state.content} /></div>;
  return <pre className="p-3 font-mono text-[13px] leading-relaxed whitespace-pre-wrap break-words text-gray-700">{state.content}</pre>;
}

/** One changed file: what it holds now (``content``) or what changed (``diff``). */
function ArtifactDetail({ artifact, workspace, mode }) {
  const { t } = useI18n();
  const meta = ARTIFACT_OP_META[artifact.op] || ARTIFACT_OP_META.modify;
  return (
    <div className="flex-1 min-w-0 flex flex-col min-h-0">
      <div className="px-3 py-2 border-b border-gray-100 flex items-center gap-2 shrink-0">
        <span className={`w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold shrink-0 ${meta.cls}`}>
          {meta.label}
        </span>
        <span className="flex-1 min-w-0 text-sm font-medium text-gray-700 truncate" title={artifact.path}>{artifact.path}</span>
        <span className="flex items-center gap-1.5 shrink-0 text-[10px] font-mono">
          {artifact.additions > 0 && <span className="text-emerald-600">+{artifact.additions}</span>}
          {artifact.deletions > 0 && <span className="text-red-600">−{artifact.deletions}</span>}
        </span>
      </div>
      <div className="flex-1 min-h-0 overflow-auto" data-testid="artifact-detail">
        {mode === 'content' ? (
          <ArtifactContent key={`${workspace}:${artifact.path}`} workspace={workspace} path={artifact.path} />
        ) : artifact.binary ? (
          <div className="px-3 py-2 text-xs text-gray-400 italic">
            {artifact.truncated ? t('chat.fileTooLarge') : t('chat.binaryFile')}
          </div>
        ) : (
          <div className="py-2"><DiffView diff={artifact.diff} /></div>
        )}
      </div>
    </div>
  );
}

/**
 * The files the runs changed and the views they made: a list on the left,
 * the selected one on the right. ``mode`` is the column's switch: ``content``
 * (the default) shows each file as it is now, so a deleted file is not
 * listed; ``diff`` shows what every run changed, deletions included. A view
 * is drawn whole in either.
 */
function ArtifactsPanel({ artifacts, views = [], workspace = null, mode = 'content' }) {
  const { t } = useI18n();
  const items = useMemo(
    () => Object.values(artifacts || {})
      .filter((a) => mode === 'diff' || a.op !== 'delete')
      .sort((a, b) => (a.path || '').localeCompare(b.path || '')),
    [artifacts, mode],
  );
  const totals = useMemo(() => {
    let add = 0, del = 0;
    for (const a of items) { add += a.additions || 0; del += a.deletions || 0; }
    return { add, del };
  }, [items]);
  // `file:<path>` or `view:<id>`; the newest view, else the first file, until picked.
  const [picked, setPicked] = useState(null);
  const fallback = views.length ? `view:${views[views.length - 1].view_id}` : items.length ? `file:${items[0].path}` : null;
  const stillThere = picked && (
    (picked.startsWith('file:') && items.some((a) => `file:${a.path}` === picked))
    || (picked.startsWith('view:') && views.some((v) => `view:${v.view_id}` === picked)));
  const selected = stillThere ? picked : fallback;

  if (!items.length && !views.length) {
    return (
      <div className="flex-1 overflow-y-auto p-4">
        <p className="text-xs text-gray-500 italic">{t('chat.noArtifactsYet')}</p>
      </div>
    );
  }
  const selectedFile = selected?.startsWith('file:') ? items.find((a) => `file:${a.path}` === selected) : null;
  const selectedView = selected?.startsWith('view:') ? views.find((v) => `view:${v.view_id}` === selected) : null;
  return (
    <div className="flex-1 min-h-0 flex overflow-hidden">
      <div className="w-52 flex-shrink-0 border-r border-gray-200 overflow-y-auto py-2">
        {views.length > 0 && (
          <section>
            <div className="flex items-center gap-1.5 px-3 pb-1 text-xs font-semibold text-gray-600">
              <LayoutGrid className="w-3.5 h-3.5 text-indigo-500" />
              {t('chat.viewCount', { count: views.length })}
            </div>
            {[...views].reverse().map((v) => {
              const key = `view:${v.view_id}`;
              return (
                <button key={key} type="button" onClick={() => setPicked(key)}
                  className={`w-full text-left px-3 py-1.5 text-sm truncate hover:bg-gray-50 ${
                    selected === key ? 'bg-indigo-50 text-indigo-700' : 'text-gray-700'}`} title={v.title || v.view_id}>
                  {v.title || v.view_id}
                </button>
              );
            })}
          </section>
        )}
        {items.length > 0 && (
          <section className={views.length ? 'mt-2' : ''}>
            <div className="flex items-center gap-2 px-3 pb-1 text-xs text-gray-500">
              <FileText className="w-3.5 h-3.5 text-indigo-500" />
              <span className="font-semibold text-gray-600">{t('chat.fileCount', { count: items.length })}</span>
              <span className="font-mono text-emerald-600">+{totals.add}</span>
              <span className="font-mono text-red-600">−{totals.del}</span>
            </div>
            {items.map((a) => {
              const key = `file:${a.path}`;
              const meta = ARTIFACT_OP_META[a.op] || ARTIFACT_OP_META.modify;
              return (
                <button key={key} type="button" onClick={() => setPicked(key)} data-artifact-path={a.path}
                  className={`w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-gray-50 ${
                    selected === key ? 'bg-indigo-50' : ''}`} title={a.path}>
                  <span className={`w-4 h-4 rounded flex items-center justify-center text-[9px] font-bold shrink-0 ${meta.cls}`}>
                    {meta.label}
                  </span>
                  <span className={`flex-1 min-w-0 text-sm truncate ${selected === key ? 'text-indigo-700 font-medium' : 'text-gray-700'}`}>
                    {a.path.split('/').pop()}
                  </span>
                </button>
              );
            })}
          </section>
        )}
      </div>
      {selectedFile ? (
        <ArtifactDetail key={selectedFile.path} artifact={selectedFile} workspace={workspace} mode={mode} />
      ) : selectedView ? (
        <div className="flex-1 min-w-0 overflow-auto p-3" data-testid="artifact-detail">
          <ViewCard key={selectedView.view_id} viewRef={selectedView} />
        </div>
      ) : null}
    </div>
  );
}

export { ProcessPanelContent, DiffView, ARTIFACT_OP_META, ArtifactItem, ArtifactsPanel };
