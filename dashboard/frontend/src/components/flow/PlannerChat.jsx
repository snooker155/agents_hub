import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { ListTodo, Brain, Trash2, Square, MessageSquare, PanelRightClose, Send } from 'lucide-react';
import { FeedItem } from './ChatFeed';
import ContextMeter from '../ContextMeter';
import { useContextUsage } from '../contextUsage';
import { useI18n } from '../../i18n';
import { useInlineChatOpen } from '../pageChat/pageChat';
import {
  getProjectTasksChat, clearProjectTasksChat,
  streamProjectTasksGenerate, stopProjectGraphChat,
} from '../../api';

// Sidebar chat panel for the Tasks tab (same style as the architect chat). The
// "Generate tasks from graphs" button (top) runs the default plan; the chat
// input (bottom) sends free-text refinements. Shows thinking + tool calls live
// and replays the full session on reload. Calls onGenerated when tasks change.
function PlannerChat({ projectId, onGenerated, onClose, toolbarTarget }) {
  const { t } = useI18n();
  // Drawn only while the Tasks tab has the planner open, so being mounted is
  // being on screen: the floating launcher stands down meanwhile.
  useInlineChatOpen();
  const [feed, setFeed] = useState([]);
  // Planning sessions run long and re-send their transcript each turn: the
  // meter under the composer says how much of the model's window is left.
  const { usage: contextUsage, observe: observeContext, reset: resetContext } = useContextUsage();
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [showThinking, setShowThinking] = useState(true);
  const [input, setInput] = useState('');
  const [error, setError] = useState('');
  const abortRef = useRef(null);
  const feedRef = useRef(null);
  const textareaRef = useRef(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const { data } = await getProjectTasksChat(projectId);
      const trace = data.trace;
      if (Array.isArray(trace) && trace.length) setFeed(trace);
      else setFeed((data.messages || []).map((x) => ({ k: x.role, text: x.content })));
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('flowPlannerChat.loadFailed'));
      setFeed([]);
    }
  }, [projectId, t]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => () => { if (abortRef.current) abortRef.current.abort(); }, []);
  useEffect(() => { if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight; }, [feed]);

  // Run one planning turn. `message` empty → the default "generate from graphs".
  const run = useCallback(async (message) => {
    if (busy) return;
    setBusy(true); setError('');
    const shown = message || t('flowPlannerChat.generatePrompt');
    setFeed((f) => [...f, { k: 'user', text: shown }]);
    const append = (item) => setFeed((f) => [...f, item]);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await streamProjectTasksGenerate({
        projectId, message: message || undefined, signal: ac.signal,
        onEvent: (ev) => {
          observeContext(ev);
          switch (ev.type) {
            case 'tool_start':
              append({ k: 'tool', tool: ev.tool, status: 'running' });
              break;
            case 'think':
              if (ev.content) append({ k: 'thinking', text: ev.content });
              break;
            case 'thinking':
            case 'native_reasoning': {
              const t = ev.message || ev.content || '';
              if (t && !t.startsWith('[')) append({ k: 'thinking', text: t });
              break;
            }
            case 'error':
              append({ k: 'error', text: ev.error || 'error' });
              break;
            case 'stopped':
              append({ k: 'tool', tool: t('flowPlannerChat.stoppedByYou'), status: 'done' });
              break;
            case 'message':
              if (ev.content) append({ k: 'assistant', text: ev.content });
              break;
            case 'done':
              if (ev.created > 0 && typeof onGenerated === 'function') onGenerated();
              break;
            default:
              break;
          }
        },
      });
    } catch (e) {
      if (e.name !== 'AbortError') append({ k: 'error', text: e.message || t('flowPlannerChat.generationFailed') });
    } finally {
      setBusy(false);
      setStopping(false);
      abortRef.current = null;
    }
  }, [busy, t, projectId, onGenerated, observeContext]);

  const onSend = useCallback(() => {
    const m = input.trim();
    if (!m) return;
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    run(m);
  }, [input, run]);

  const resizeTextarea = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
  }, []);

  const stop = useCallback(async () => {
    if (!busy || stopping) return;
    setStopping(true);
    try {
      await stopProjectGraphChat(projectId);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('flowPlannerChat.stopFailed'));
      setStopping(false);
    }
  }, [busy, stopping, projectId, t]);

  const clearChat = useCallback(async () => {
    if (busy) return;
    setError('');
    try {
      await clearProjectTasksChat(projectId);
      setFeed([]);
      resetContext();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('flowPlannerChat.clearFailed'));
    }
  }, [busy, projectId, resetContext, t]);

  // "Generate tasks" button — portaled up next to "New Task" in the tab toolbar
  // when a target is provided, otherwise shown inline in the planner header.
  const generateButton = (
    <button onClick={() => run()} disabled={busy}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium text-emerald-600 hover:bg-emerald-50 disabled:opacity-40"
      title={t('flowPlannerChat.createAFullTaskPlan')}>
      <ListTodo className="w-4 h-4" /> {t('flowPlannerChat.generateFromArchitecture')}
    </button>
  );

  return (
    <div className="w-[28rem] shrink-0 flex flex-col bg-white border border-gray-200 rounded-xl overflow-hidden">
      {toolbarTarget && createPortal(generateButton, toolbarTarget)}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 gap-2">
        <div className="flex items-center gap-1.5 text-sm font-medium text-gray-700 min-w-0">
          <MessageSquare className={`w-4 h-4 shrink-0 ${busy ? 'text-emerald-500 animate-pulse' : 'text-gray-400'}`} /> {t('flowPlannerChat.taskPlanner')}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!toolbarTarget && (
            <button onClick={() => run()} disabled={busy}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium text-emerald-600 hover:bg-emerald-50 disabled:opacity-40"
              title={t('flowPlannerChat.createAFullTaskPlan')}>
              <ListTodo className="w-4 h-4" /> {t('flowPlannerChat.generateFromArchitecture')}
            </button>
          )}
          <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none"
            title={t('flowPlannerChat.showOrHideTheAgent')}>
            <input type="checkbox" checked={showThinking}
              onChange={(e) => setShowThinking(e.target.checked)}
              className="w-3.5 h-3.5 accent-indigo-600 cursor-pointer" />
            <Brain className="w-3.5 h-3.5 text-amber-400" /> {t('flowPlannerChat.thinking')}
          </label>
          <button onClick={clearChat} disabled={busy}
            className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-50 px-2 py-1 rounded-md disabled:opacity-40"
            title={t('flowPlannerChat.clearThePlannerChatTasks')}>
            <Trash2 className="w-3.5 h-3.5" /> {t('flowPlannerChat.clearChat')}
          </button>
          {onClose && (
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600" title={t('flowPlannerChat.hidePlanner')}>
              <PanelRightClose className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {error ? <div className="px-4 py-2 text-xs text-red-600 bg-red-50 border-b border-red-100">{error}</div> : null}

      <div ref={feedRef} className="flex-1 min-h-0 overflow-y-auto px-3 py-2 space-y-2">
        {feed.length === 0 && !busy ? (
          <div className="text-xs text-gray-400 mt-2">
            The Planner reads this project's <span className="font-medium">{t('flowPlannerChat.architecture')}</span> and{' '}
            <span className="font-medium">{t('flowPlannerChat.process')}</span> graphs and manages the task tree. Hit{' '}
            <span className="text-emerald-600 font-medium">{t('flowPlannerChat.generateTasks')}</span>, or ask for a change —
            e.g. <span className="italic">{t('flowPlannerChat.breakThePaymentsTaskInto')}</span>
          </div>
        ) : null}
        {feed.map((e, i) => (
          (showThinking || e.k !== 'thinking') ? <FeedItem key={i} e={e} /> : null
        ))}
        {busy ? <div className="text-[11px] text-emerald-500 animate-pulse">{stopping ? 'stopping…' : 'planning…'}</div> : null}
      </div>

      <div className="border-t border-gray-100 p-3">
        <ContextMeter usage={contextUsage} onClear={busy ? null : clearChat} className="mb-2" />
        <div className="flex items-center gap-3 bg-white border border-gray-300 rounded-2xl px-4 py-2.5
          focus-within:border-emerald-400 focus-within:ring-2 focus-within:ring-emerald-100 shadow-sm transition-all">
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            disabled={busy}
            onChange={(e) => { setInput(e.target.value); resizeTextarea(); }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); } }}
            placeholder={t('flowPlannerChat.askThePlannerToCreate')}
            className="flex-1 resize-none text-sm text-gray-800 placeholder-gray-400 focus:outline-none bg-transparent leading-relaxed disabled:opacity-50"
          />
          {busy ? (
            <button onClick={stop} disabled={stopping} title={t('flowPlannerChat.stopTheCurrentRun')}
              className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
              <Square className="w-3 h-3" fill="currentColor" />
            </button>
          ) : (
            <button onClick={onSend} disabled={!input.trim()} title={t('flowPlannerChat.sendEnter')}
              className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
              <Send className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        <p className="text-center text-[11px] text-gray-400 mt-1.5">
          {busy ? (stopping ? t('flowPlannerChat.stopping') : t('flowPlannerChat.pressStop')) : t('flowPlannerChat.enterToSend')}
        </p>
      </div>
    </div>
  );
}

export default PlannerChat;
