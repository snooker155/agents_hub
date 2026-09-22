/**
 * The two memory tools that earn a card of their own rather than a line in the
 * timeline: what the agent chose to remember, and what it recalled.
 */
import { SlotData } from '../SlotValue';
import { shortText } from '../processUtils';
import { useI18n } from '../../i18n';
import { parseJsonSafe } from './memoryTools';
import { TimelineToolCard } from './timeline';
import { BrainCircuit, Check, Database, FileText, ListChecks, Workflow } from 'lucide-react';
import React, { useState } from 'react';

const EPISODE_KIND_CLS = {
  decision: 'bg-blue-100 text-blue-700',
  error: 'bg-red-100 text-red-700',
  task: 'bg-emerald-100 text-emerald-700',
  observation: 'bg-gray-100 text-gray-600',
  interaction: 'bg-gray-100 text-gray-600',
};

function ExtractionRationale({ item }) {
  if (!item.reason && !item.evidence) return null;
  return (
    <div className="mt-1 space-y-0.5">
      {item.reason && <div className="text-[11px] text-gray-500 italic">{item.reason}</div>}
      {item.evidence && (
        <div className="text-[11px] text-gray-600 border-l-2 border-violet-300 pl-2">
          “{item.evidence}”
        </div>
      )}
    </div>
  );
}

function ExtractionSection({ icon: Icon, title, children }) {
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-1">
        <Icon className="w-3 h-3 text-violet-500" />
        <span className="text-[11px] font-semibold text-violet-700 uppercase tracking-wide">{title}</span>
      </div>
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}

function ModeBadge({ mode }) {
  const { t } = useI18n();
  if (!mode) return null;
  const isNew = mode === 'create';
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${isNew ? 'bg-emerald-100 text-emerald-700' : 'bg-sky-100 text-sky-700'}`}>
      {isNew ? t('chat.modeNew') : mode === 'extend' ? t('chat.modeExtend') : t('chat.modeMerge')}
    </span>
  );
}

function ExtractionProposalBody({ result }) {
  const { t } = useI18n();
  const p = result.proposal || {};
  const slots = p.slots || [];
  const notes = p.notes || [];
  const episodes = p.episodes || [];
  const triples = p.triples || [];
  const hasAny = slots.length || notes.length || episodes.length || triples.length;

  if (!hasAny) {
    return <div className="text-[11px] text-gray-500">{result.note || t('chat.nothingExtractable')}</div>;
  }
  return (
    <>
      {slots.length > 0 && (
        <ExtractionSection icon={Database} title={t('chat.structuredSlots')}>
          {slots.map((s, i) => (
            <div key={i} className="bg-white border border-gray-200 rounded-lg px-2.5 py-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-mono font-semibold text-gray-800">{s.slot}</span>
                <ModeBadge mode={s.mode} />
              </div>
              <div className="text-[11px]"><SlotData data={s.data} /></div>
              <ExtractionRationale item={s} />
            </div>
          ))}
        </ExtractionSection>
      )}
      {notes.length > 0 && (
        <ExtractionSection icon={FileText} title={t('chat.notes')}>
          {notes.map((n, i) => (
            <div key={i} className="bg-white border border-gray-200 rounded-lg px-2.5 py-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-semibold text-gray-800">{n.title}</span>
                <ModeBadge mode={n.mode} />
              </div>
              <div className="text-[11px] text-gray-600 mt-0.5">{shortText(n.content, 220)}</div>
              <ExtractionRationale item={n} />
            </div>
          ))}
        </ExtractionSection>
      )}
      {episodes.length > 0 && (
        <ExtractionSection icon={ListChecks} title={t('chat.episodes')}>
          {episodes.map((e, i) => (
            <div key={i} className="bg-white border border-gray-200 rounded-lg px-2.5 py-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] font-mono text-gray-400">#{i}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${EPISODE_KIND_CLS[e.kind] || 'bg-gray-100 text-gray-600'}`}>{e.kind}</span>
                {e.outcome && <span className="text-[10px] text-gray-500">{e.outcome}</span>}
              </div>
              <div className="text-[11px] text-gray-700 mt-0.5">{e.summary}</div>
              <ExtractionRationale item={e} />
            </div>
          ))}
        </ExtractionSection>
      )}
      {triples.length > 0 && (
        <ExtractionSection icon={Workflow} title={t('chat.relationships')}>
          {triples.map((t, i) => (
            <div key={i} className="bg-white border border-gray-200 rounded-lg px-2.5 py-1.5">
              <div className="flex items-center gap-1.5 flex-wrap text-[11px]">
                <span className="text-[10px] font-mono text-gray-400">#{i}</span>
                <span className="font-mono text-gray-800">{t.source?.name}</span>
                <span className="text-gray-400">({t.source?.type})</span>
                <span className="text-violet-600 font-medium">—{t.relation}→</span>
                <span className="font-mono text-gray-800">{t.target?.name}</span>
                <span className="text-gray-400">({t.target?.type})</span>
              </div>
              <ExtractionRationale item={t} />
            </div>
          ))}
        </ExtractionSection>
      )}
    </>
  );
}

function SaveResultBody({ result }) {
  const { t } = useI18n();
  const persisted = result.persisted || {};
  const lines = [];
  if ((persisted.slots_created || []).length) lines.push([t('chat.save.slotsCreated'), persisted.slots_created.join(', ')]);
  if ((persisted.slots_merged || []).length) lines.push([t('chat.save.slotsMerged'), persisted.slots_merged.join(', ')]);
  if ((persisted.notes_created || []).length) lines.push([t('chat.save.notesCreated'), persisted.notes_created.join(', ')]);
  if ((persisted.notes_extended || []).length) lines.push([t('chat.save.notesExtended'), persisted.notes_extended.join(', ')]);
  if ((persisted.notes_skipped || []).length) lines.push([t('chat.save.notesSkipped'), persisted.notes_skipped.join(', ')]);
  if (persisted.episodes_recorded) lines.push([t('chat.save.episodesRecorded'), String(persisted.episodes_recorded)]);
  if (persisted.edges_added) lines.push([t('chat.save.edgesAdded'), String(persisted.edges_added)]);
  if ((result.dropped || []).length) lines.push([t('chat.save.dropped'), result.dropped.join(', ')]);
  return (
    <>
      {lines.length === 0 && <div className="text-[11px] text-gray-500">{result.note || t('chat.nothingSaved')}</div>}
      {lines.map(([label, value]) => (
        <div key={label} className="flex gap-2 text-[11px]">
          <span className="text-gray-500 flex-shrink-0">{label}:</span>
          <span className="text-gray-800 font-medium break-all">{value}</span>
        </div>
      ))}
    </>
  );
}

function ExtractionToolCard({ entry }) {
  const { t } = useI18n();
  const [showRaw, setShowRaw] = useState(false);
  const isSave = entry.tool === 'save_extraction';

  if (entry.running || entry.output == null) {
    return (
      <div className="rounded-lg border border-violet-200 bg-violet-50/50 px-3 py-2 flex items-center gap-2">
        <BrainCircuit className="w-3.5 h-3.5 text-violet-500" />
        <span className="text-xs font-semibold text-violet-700">
          {isSave ? t('chat.savingExtraction') : t('chat.extractingKnowledge')}
        </span>
        <span className="flex gap-1 ml-1">
          {[0, 150, 300].map((d) => (
            <span key={d} className="w-1 h-1 bg-violet-400 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />
          ))}
        </span>
      </div>
    );
  }

  const result = parseJsonSafe(entry.output);
  if (!result) return <TimelineToolCard entry={entry} />;

  const failed = result.ok === false;
  const theme = failed
    ? 'border-red-200 bg-red-50/40'
    : isSave ? 'border-emerald-200 bg-emerald-50/40' : 'border-violet-200 bg-violet-50/40';
  const headerColor = failed ? 'text-red-700' : isSave ? 'text-emerald-700' : 'text-violet-700';
  const title = failed
    ? (isSave ? t('chat.saveFailed') : t('chat.extractionFailed'))
    : isSave ? t('chat.savedToMemory') : t('chat.extractionProposal');

  return (
    <div className={`rounded-lg border ${theme}`}>
      <div className="flex items-center gap-2 px-3 py-2">
        {isSave && !failed
          ? <Check className="w-3.5 h-3.5 text-emerald-600 flex-shrink-0" />
          : <BrainCircuit className={`w-3.5 h-3.5 flex-shrink-0 ${failed ? 'text-red-500' : 'text-violet-500'}`} />}
        <span className={`text-xs font-semibold ${headerColor}`}>{title}</span>
        {result.extraction_id && (
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-white/80 border border-gray-200 text-gray-600">
            id: {result.extraction_id}
          </span>
        )}
        <button
          type="button"
          onClick={() => setShowRaw((v) => !v)}
          className="ml-auto text-[10px] text-gray-400 hover:text-gray-600"
        >
          {showRaw ? 'formatted' : 'raw'}
        </button>
      </div>
      <div className="px-3 pb-2.5 space-y-2.5">
        {showRaw ? (
          <pre className="text-[10px] text-gray-600 whitespace-pre-wrap break-all bg-white border border-gray-200 rounded-lg p-2 max-h-72 overflow-y-auto">
            {JSON.stringify(result, null, 2)}
          </pre>
        ) : isSave ? (
          <SaveResultBody result={result} />
        ) : (
          <ExtractionProposalBody result={result} />
        )}
        {(result.errors || []).length > 0 && (
          <div className="text-[11px] text-red-600 space-y-0.5">
            {result.errors.map((err, i) => <div key={i}>⚠ {err}</div>)}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recall tool card — shows the memory-layer cascade and per-result provenance
// ---------------------------------------------------------------------------
const RECALL_LAYER_LABEL = {
  structured_slots: 'Slots',
  notes: 'Notes',
  graph: 'Graph',
  rag: 'RAG',
};

const RECALL_SOURCE_BADGE = {
  structured: ['slot', 'bg-indigo-100 text-indigo-700'],
  note: ['note', 'bg-amber-100 text-amber-700'],
  graph: ['graph', 'bg-emerald-100 text-emerald-700'],
  rag: ['rag', 'bg-purple-100 text-purple-700'],
};

function RecallResultRow({ res }) {
  const { t } = useI18n();
  const [badge, badgeCls] = RECALL_SOURCE_BADGE[res.source] || [res.source, 'bg-gray-100 text-gray-600'];
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-2.5 py-1.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${badgeCls}`}>{badge}</span>
        {res.source === 'structured' && <span className="text-xs font-mono font-semibold text-gray-800">{res.slot}</span>}
        {res.source === 'note' && <span className="text-xs font-semibold text-gray-800">{res.title}</span>}
        {res.source === 'graph' && (
          <span className="text-xs font-mono text-gray-800">
            {res.node?.name} <span className="text-gray-400 font-sans">({res.node?.type})</span>
          </span>
        )}
        {res.source === 'rag' && (
          <span className="text-xs font-mono text-gray-700">
            {res.file_id || 'document'}
            {res.score != null && <span className="text-gray-400 font-sans"> · {t('chat.score')} {res.score}</span>}
          </span>
        )}
      </div>
      {res.source === 'structured' && res.data && (
        <div className="text-[11px]"><SlotData data={res.data} /></div>
      )}
      {(res.source === 'note' || res.source === 'rag') && (
        <div className="text-[11px] text-gray-600 mt-0.5">{shortText(res.content || res.text || '', 200)}</div>
      )}
      {(res.relations || []).length > 0 && (
        <div className="mt-1 space-y-0.5">
          {res.relations.slice(0, 5).map((rel, i) => (
            <div key={i} className="text-[11px] font-mono text-emerald-700">{rel}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function RecallToolCard({ entry }) {
  const { t } = useI18n();
  const [showRaw, setShowRaw] = useState(false);

  if (entry.running || entry.output == null) {
    return (
      <div className="rounded-lg border border-sky-200 bg-sky-50/50 px-3 py-2 flex items-center gap-2">
        <Database className="w-3.5 h-3.5 text-sky-500" />
        <span className="text-xs font-semibold text-sky-700">{t('chat.recallingFromMemory')}</span>
        <span className="flex gap-1 ml-1">
          {[0, 150, 300].map((d) => (
            <span key={d} className="w-1 h-1 bg-sky-400 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />
          ))}
        </span>
      </div>
    );
  }

  const result = parseJsonSafe(entry.output);
  // Render the rich card whenever the output is a recall result; the trace
  // row is optional so outputs from older backends still get the card.
  if (!result || result.ok === false || !Array.isArray(result.results)) {
    return <TimelineToolCard entry={entry} />;
  }

  return (
    <div className="rounded-lg border border-sky-200 bg-sky-50/40">
      <div className="flex items-center gap-2 px-3 py-2 flex-wrap">
        <Database className="w-3.5 h-3.5 text-sky-500 flex-shrink-0" />
        <span className="text-xs font-semibold text-sky-700">{t('chat.memoryRecall')}</span>
        {result.query && (
          <span className="text-[11px] text-gray-600 truncate">“{shortText(result.query, 60)}”</span>
        )}
        {result.pool && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/80 border border-gray-200 text-gray-500">
            pool: {result.pool}
          </span>
        )}
        <button
          type="button"
          onClick={() => setShowRaw((v) => !v)}
          className="ml-auto text-[10px] text-gray-400 hover:text-gray-600"
        >
          {showRaw ? 'formatted' : 'raw'}
        </button>
      </div>
      <div className="px-3 pb-2.5 space-y-2">
        {/* Search cascade: layer → layer with hit counts */}
        {Array.isArray(result.trace) && result.trace.length > 0 && (
        <div className="flex items-center gap-1 flex-wrap">
          {result.trace.map((t, i) => (
            <React.Fragment key={t.layer}>
              {i > 0 && <span className="text-gray-300 text-[10px]">→</span>}
              <span
                title={t.skipped ? `skipped: ${t.skipped}` : (t.searched != null ? `${t.searched} searched` : undefined)}
                className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                  t.hits > 0
                    ? 'bg-sky-100 text-sky-700'
                    : t.skipped
                      ? 'bg-gray-100 text-gray-400 line-through'
                      : 'bg-gray-100 text-gray-500'
                }`}
              >
                {RECALL_LAYER_LABEL[t.layer] || t.layer} {t.skipped ? '' : `· ${t.hits}`}
              </span>
            </React.Fragment>
          ))}
        </div>
        )}
        {showRaw ? (
          <pre className="text-[10px] text-gray-600 whitespace-pre-wrap break-all bg-white border border-gray-200 rounded-lg p-2 max-h-72 overflow-y-auto">
            {JSON.stringify(result, null, 2)}
          </pre>
        ) : result.found ? (
          (result.results || []).map((res, i) => <RecallResultRow key={i} res={res} />)
        ) : (
          <div className="text-[11px] text-gray-500">{result.note || t('chat.nothingFoundInMemory')}</div>
        )}
      </div>
    </div>
  );
}

export { EPISODE_KIND_CLS, ExtractionRationale, ExtractionSection, ModeBadge, ExtractionProposalBody, SaveResultBody, ExtractionToolCard, RECALL_LAYER_LABEL, RECALL_SOURCE_BADGE, RecallResultRow, RecallToolCard };
