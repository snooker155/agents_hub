import { useCallback, useEffect, useRef, useState } from 'react';
import { Radio, ChevronDown, ChevronRight, Brain, Wrench, AlertCircle } from 'lucide-react';
import { useChannel } from './stream';
import { useI18n } from '../i18n';

/*
 * Live view of agent runs happening on one session channel.
 *
 * Chat gets its live text from the `/api/chat/stream` response body, which only
 * exists for a request the browser itself made. Everything else — a task worker,
 * a flow node, a node-managed run — executes in another process, so its output
 * reaches the browser over the session's SSE channel instead (published by
 * `agents.callbacks.SessionPublishCallback`). This component renders that
 * channel: one block per run, with the answer text as it streams, the model's
 * thinking, and the tool calls in between.
 *
 * Token events only arrive when agent streaming is enabled in Settings; with it
 * off the same block still fills in with tool calls, thinking and the final
 * answer, just in steps rather than continuously.
 */

// Keep a bounded amount of streamed text per run: a long agent run can emit
// hundreds of KB, and this view is a live tail, not the archive (the run log
// and the Execution tab hold the full record).
const MAX_TEXT = 40000;

const clip = (text) => (text.length > MAX_TEXT ? text.slice(text.length - MAX_TEXT) : text);

/**
 * One run's live state, rebuilt from the catch-up snapshot the server keeps
 * (`common.live_runs`). A page that opens while a run is half-way through
 * starts from this rather than from the middle of the broadcast.
 */
function seedRuns(seed) {
  if (!seed || !seed.run_id) return [];
  return [{
    run_id: String(seed.run_id),
    agent_id: seed.agent_id || '',
    text: clip(String(seed.text || '')),
    thinking: (seed.thinking || []).map((entry) => String(entry?.content ?? entry ?? '')),
    tools: (seed.tools || []).map((tool) => ({ ...tool })),
    errors: seed.error ? [String(seed.error)] : [],
    done: seed.status !== 'running',
    ok: seed.status === 'finished',
    usage: seed.usage || null,
    duration_ms: seed.duration_ms ?? null,
    started_at: seed.started_at ? seed.started_at * 1000 : Date.now(),
  }];
}

/**
 * Subscribe to `sessionId` and accumulate its runs.
 * Returns `{ runs, activeCount }`; `runs` is oldest-first.
 *
 * The runs are stored together with the session they belong to, so switching
 * sessions drops the previous one's blocks without an effect that resets state
 * (which would render the stale list for a frame before clearing it).
 *
 * `runId` narrows the panel to one run, for a page that is about that run and
 * not about everything its session happens to be doing. `seed` is where the
 * accumulation starts, so what streamed before the page opened is not lost; the
 * caller is expected to have it in hand before mounting, since events arriving
 * before a seed could not be told apart from the text already in it.
 */
function useLiveRunStream(sessionId, { runId = null, seed = null } = {}) {
  const key = `${sessionId || ''}|${runId || ''}`;
  const [state, setState] = useState(() => ({ sessionId: key, runs: seedRuns(seed) }));
  const runs = state.sessionId === key ? state.runs : seedRuns(seed);

  const upsert = useCallback((runId, mutate) => {
    if (!runId) return;
    setState((prev) => {
      const list = prev.sessionId === key ? prev.runs : [];
      const idx = list.findIndex((r) => r.run_id === runId);
      if (idx === -1) {
        return { sessionId: key, runs: [...list, mutate({
          run_id: runId, agent_id: '', text: '', thinking: [], tools: [],
          errors: [], done: false, ok: null, usage: null, duration_ms: null,
          started_at: Date.now(),
        })] };
      }
      const next = [...list];
      next[idx] = mutate(next[idx]);
      return { sessionId: key, runs: next };
    });
  }, [key]);

  useChannel(sessionId || null, useCallback((ev) => {
    if (!ev || !ev.type || ev.type === 'heartbeat') return;
    const eventRunId = ev.run_id;
    // A session channel carries every run on that session; a panel about one
    // run wants only its own.
    if (runId && String(eventRunId || '') !== String(runId)) return;

    switch (ev.type) {
      case 'meta':
        upsert(eventRunId, (r) => ({ ...r, agent_id: ev.agent_id || r.agent_id }));
        break;
      case 'token':
        upsert(eventRunId, (r) => ({ ...r, text: clip(r.text + (ev.token || '')) }));
        break;
      case 'thinking':
        upsert(eventRunId, (r) => ({ ...r, thinking: [...r.thinking, String(ev.message || ev.content || '')] }));
        break;
      case 'tool_start':
        upsert(eventRunId, (r) => ({
          ...r,
          tools: [...r.tools, { step: ev.step, tool: ev.tool, input: ev.input || '', output: null, error: null }],
        }));
        break;
      case 'tool_end':
        upsert(eventRunId, (r) => {
          if (!r.tools.length) return r;
          const tools = [...r.tools];
          tools[tools.length - 1] = { ...tools[tools.length - 1], output: ev.output || '' };
          return { ...r, tools };
        });
        break;
      case 'tool_error':
        upsert(eventRunId, (r) => {
          if (!r.tools.length) return { ...r, errors: [...r.errors, String(ev.error || '')] };
          const tools = [...r.tools];
          tools[tools.length - 1] = { ...tools[tools.length - 1], error: String(ev.error || '') };
          return { ...r, tools };
        });
        break;
      case 'error':
        upsert(eventRunId, (r) => ({ ...r, errors: [...r.errors, String(ev.error || '')] }));
        break;
      case 'done':
        upsert(eventRunId, (r) => ({
          ...r,
          // The streamed text is what the user watched appear; fall back to the
          // final response only when nothing streamed (streaming off, or a run
          // that answered in one shot).
          text: clip(r.text.trim() ? r.text : String(ev.response || '')),
          done: true,
          ok: !!ev.ok,
          usage: ev.usage || null,
          duration_ms: ev.duration_ms ?? null,
        }));
        break;
      default:
        break;
    }
  }, [upsert, runId]));

  return { runs, activeCount: runs.filter((r) => !r.done).length };
}

function ToolRow({ tool }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const hasDetail = !!(tool.input || tool.output || tool.error);
  return (
    <div className="text-xs">
      <button
        type="button"
        onClick={() => hasDetail && setOpen((o) => !o)}
        className={`flex items-center gap-1.5 text-left w-full ${hasDetail ? 'cursor-pointer' : 'cursor-default'}`}
      >
        {hasDetail
          ? (open ? <ChevronDown className="w-3 h-3 text-gray-400" /> : <ChevronRight className="w-3 h-3 text-gray-400" />)
          : <span className="w-3" />}
        <Wrench className={`w-3 h-3 ${tool.error ? 'text-red-500' : 'text-indigo-500'}`} />
        <span className="font-mono text-gray-700">{tool.tool}</span>
        {tool.output === null && !tool.error && (
          <span className="text-[10px] text-indigo-500 animate-pulse">{t('liveRunStream.running')}</span>
        )}
      </button>
      {open && (
        <div className="ml-6 mt-1 space-y-1">
          {tool.input && <pre className="whitespace-pre-wrap break-words text-[11px] text-gray-500 bg-gray-50 rounded px-2 py-1">{tool.input}</pre>}
          {tool.output && <pre className="whitespace-pre-wrap break-words text-[11px] text-gray-600 bg-gray-50 rounded px-2 py-1">{tool.output}</pre>}
          {tool.error && <pre className="whitespace-pre-wrap break-words text-[11px] text-red-600 bg-red-50 rounded px-2 py-1">{tool.error}</pre>}
        </div>
      )}
    </div>
  );
}

function RunBlock({ run }) {
  const { t } = useI18n();
  const [showThinking, setShowThinking] = useState(false);
  return (
    <div className={`rounded-lg border p-3 space-y-2 ${run.done ? 'border-gray-200 bg-white' : 'border-indigo-200 bg-indigo-50/40'}`}>
      <div className="flex items-center gap-2 text-xs">
        <span className={`w-2 h-2 rounded-full ${run.done ? (run.ok ? 'bg-green-500' : 'bg-red-500') : 'bg-indigo-500 animate-pulse'}`} />
        <span className="font-medium text-gray-700">{run.agent_id || 'agent'}</span>
        <span className="font-mono text-[10px] text-gray-400">{String(run.run_id).slice(0, 8)}</span>
        {run.done && run.duration_ms != null && (
          <span className="text-[10px] text-gray-400 ml-auto">{Math.round(run.duration_ms / 100) / 10}s</span>
        )}
      </div>

      {run.thinking.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowThinking((o) => !o)}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-700"
          >
            {showThinking ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            <Brain className="w-3 h-3 text-violet-500" />
            Thinking ({run.thinking.length})
          </button>
          {showThinking && (
            <div className="mt-1 space-y-1">
              {run.thinking.map((t, i) => (
                <pre key={i} className="whitespace-pre-wrap break-words text-[11px] text-violet-700 bg-violet-50 rounded px-2 py-1">{t}</pre>
              ))}
            </div>
          )}
        </div>
      )}

      {run.tools.length > 0 && (
        <div className="space-y-1">
          {run.tools.map((t, i) => <ToolRow key={i} tool={t} />)}
        </div>
      )}

      {run.errors.map((e, i) => (
        <div key={i} className="flex items-start gap-1.5 text-xs text-red-600">
          <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" />
          <span className="break-words">{e}</span>
        </div>
      ))}

      {run.text && (
        <pre className="whitespace-pre-wrap break-words text-xs text-gray-800 leading-relaxed">
          {run.text}
          {!run.done && <span className="inline-block w-1.5 h-3 align-middle bg-indigo-500 animate-pulse ml-0.5" />}
        </pre>
      )}
      {!run.text && !run.done && run.tools.length === 0 && run.thinking.length === 0 && (
        <p className="text-xs text-gray-400 italic">{t('liveRunStream.waitingForTheModel')}</p>
      )}
    </div>
  );
}

/**
 * Live panel for a session. Renders nothing when the session has produced no
 * events yet, so it can be dropped above existing content without leaving an
 * empty box on pages where nothing is running.
 */
export default function LiveRunStream({ sessionId, runId = null, seed = null,
                                       title, className = '' }) {
  const { t } = useI18n();
  const { runs, activeCount } = useLiveRunStream(sessionId, { runId, seed });
  const heading = title ?? t('liveRunStream.liveOutput');
  const endRef = useRef(null);

  // Follow the tail while something is running; leave the scroll alone once the
  // last run finished so the user can read back without being yanked down.
  useEffect(() => {
    if (activeCount > 0) endRef.current?.scrollIntoView({ block: 'nearest' });
  }, [runs, activeCount]);

  if (!sessionId || runs.length === 0) return null;

  return (
    <div className={`bg-white rounded-xl shadow-sm border border-gray-100 p-4 ${className}`}>
      <div className="flex items-center gap-2 mb-3">
        <Radio className={`w-4 h-4 ${activeCount > 0 ? 'text-indigo-500 animate-pulse' : 'text-gray-400'}`} />
        <h3 className="text-sm font-semibold text-gray-800">{heading}</h3>
        {activeCount > 0 && (
          <span className="text-[10px] font-medium text-indigo-600 bg-indigo-50 border border-indigo-200 rounded-full px-2 py-0.5">
            {activeCount} running
          </span>
        )}
      </div>
      <div className="space-y-2 max-h-[28rem] overflow-y-auto">
        {runs.map((r) => <RunBlock key={r.run_id} run={r} />)}
        <div ref={endRef} />
      </div>
    </div>
  );
}
