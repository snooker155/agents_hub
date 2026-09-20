import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import { useChannel, useStream } from '../components/stream';
import ViewCard from '../views/ViewCard';
import { getAgents, getMessageInsights, getWorkspace, getProjects, getAgentDefinition, stopMessage, getTelegramBindings, sendTelegramMessage, listFlows, getTeams, getSessions, getSessionMessages, streamChat, getContextKinds } from '../api';
import ContextMeter from '../components/ContextMeter';
import { trimBubbleText } from '../lib/chatText';
import {
  PlusCircle,
  Send,
  StopCircle,
  Bot,
  User,
  Trash2,
  MessageSquare,
  ChevronDown,
  ChevronUp,
  Copy,
  Check,
  AlertCircle,
  FileText,
  RefreshCw,
  X,
  Paperclip,
  FolderGit2,
  Terminal,
  Zap,
  Send as SendIcon,
  Workflow,
  BrainCircuit,
  ListChecks,
  Database,
  Repeat,
  UsersRound,
  Link2,
  ArrowUpRight,
  Upload,
  Radio,
} from 'lucide-react';
import ContextEntityPicker from '../components/ContextEntityPicker';
import ProcessGraph, { TokenPill } from '../components/ProcessGraph';
import { SKILL_TOOL, shortText, fmtDurationMs } from '../components/processUtils';
import { SlotData } from '../components/SlotValue';
import { useI18n, translate, LANGUAGES } from '../i18n';
import { useConversationStore } from '../components/chatStore';
import { useLiveChatTurn } from '../components/chatLiveTurn';

// ---------------------------------------------------------------------------
// Page-local preferences
// ---------------------------------------------------------------------------
// The conversations themselves are not here: they are service records, stored
// server-side and reached through `useConversationStore` (components/chatStore.js).
// What stays in the browser is what is true of this browser only — whether a
// panel is open, which view mode was last used.
//
// Whether the agent-process panel is open. Persisted so navigating away from the
// Chat page and back (which unmounts/remounts this component) keeps it open.
const PROCESS_OPEN_KEY = 'agent_hub_chat_process_open';
// 'chat' vs 'build' view mode, persisted across navigation for the same reason.
const VIEW_MODE_KEY = 'agent_hub_chat_view_mode';
const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024; // 5 MB
const MAX_ATTACHMENT_COUNT = 6;
// Attached hub entities are resolved server-side and can each render into
// thousands of characters, so the composer caps them the way it caps files.
// Mirrors chat/references.py's MAX_REFERENCES.
const MAX_REFERENCE_COUNT = 10;

// Live reasoning is a ticker showing only the tail, so the buffer never needs to
// grow past a few screens' worth; capping it keeps a long chain-of-thought from
// piling megabytes of dead text into React state.
const MAX_LIVE_THOUGHT_CHARS = 4000;

function appendLiveThought(prev, delta) {
  const text = `${prev || ''}${delta || ''}`;
  return text.length > MAX_LIVE_THOUGHT_CHARS ? text.slice(-MAX_LIVE_THOUGHT_CHARS) : text;
}

// Merge one streamed `artifact` event into a message's file list. Metadata only
// (the diff body lives in the session-scoped `artifacts` map, which is far too
// large to store per message). Last write per path wins, but a file keeps the
// position it was first touched at, so the list reads in execution order.
function mergeMessageFile(files, event) {
  const list = files || [];
  const idx = list.findIndex((f) => f.path === event.path);
  const entry = {
    op: event.op,
    path: event.path,
    additions: event.additions || 0,
    deletions: event.deletions || 0,
  };
  if (idx === -1) return [...list, entry];
  const next = [...list];
  // A file the agent created and then edited again is still an "add" overall.
  if (next[idx].op === 'add' && entry.op === 'modify') entry.op = 'add';
  next[idx] = entry;
  return next;
}

// ---------------------------------------------------------------------------
// Slash commands
// ---------------------------------------------------------------------------
// Descriptions resolve through i18n at render time — the command names
// themselves are typed by the user and stay as they are.
const GLOBAL_COMMANDS = [
  { name: '/help', descriptionKey: 'chat.commands.help', template: '/help' },
  { name: '/clear', descriptionKey: 'chat.commands.clear', template: '/clear' },
  { name: '/new', descriptionKey: 'chat.commands.new', template: '/new' },
  { name: '/config', descriptionKey: 'chat.commands.config', template: '/config' },
];

function genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}


// ---------------------------------------------------------------------------
// A structured-response block (<<<ui>>>{json}<<<end>>>). The backend strips it
// from the final response, but during streaming the raw tokens still carry it;
// strip it from any fallback content so the markers never show in the bubble.
const UI_BLOCK_RE = /<<<\s*ui\s*>>>[\s\S]*?<<<\s*\/?\s*end\s*>>>/gi;
const stripUiBlock = (s) => (s || '').replace(UI_BLOCK_RE, '').trim();

// Markdown-ish renderer (no external deps)
// ---------------------------------------------------------------------------
function CopyButton({ text }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <button
      onClick={copy}
      className="absolute top-2 right-2 p-1 rounded text-gray-400 hover:text-gray-200 transition-colors"
      title={t('chat.copy')}
    >
      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  );
}

// Renders plain text segments with inline code and markdown table support
function renderTableAwareText(text, keyPrefix = '') {
  const lines = text.split('\n');
  const elements = [];
  let i = 0;
  let plainBuf = [];
  let elemIdx = 0;

  const renderInlineText = (str, key) => {
    const inlineParts = str.split(/(`[^`]+`)/g);
    return (
      <span key={key}>
        {inlineParts.map((ip, j) => {
          if (ip.startsWith('`') && ip.endsWith('`') && ip.length > 2) {
            return (
              <code key={j} className="bg-gray-100 text-indigo-700 px-1 py-0.5 rounded text-xs">
                {ip.slice(1, -1)}
              </code>
            );
          }
          return ip.split('\n').map((line, k, arr) => (
            <React.Fragment key={`${j}-${k}`}>
              {line}
              {k < arr.length - 1 && <br />}
            </React.Fragment>
          ));
        })}
      </span>
    );
  };

  const flushPlain = () => {
    if (!plainBuf.length) return;
    const content = plainBuf.join('\n');
    plainBuf = [];
    elements.push(renderInlineText(content, `${keyPrefix}p${elemIdx++}`));
  };

  while (i < lines.length) {
    const trimmed = lines[i].trim();
    const nextTrimmed = i + 1 < lines.length ? lines[i + 1].trim() : '';

    if (
      trimmed.startsWith('|') &&
      trimmed.endsWith('|') &&
      nextTrimmed.startsWith('|') &&
      /^\|[\s\-:|]+\|$/.test(nextTrimmed)
    ) {
      flushPlain();
      const headers = trimmed.split('|').slice(1, -1).map((h) => h.trim());
      i += 2; // skip header and separator rows
      const rows = [];
      while (i < lines.length) {
        const rowLine = lines[i].trim();
        if (rowLine.startsWith('|') && rowLine.endsWith('|')) {
          rows.push(rowLine.split('|').slice(1, -1).map((c) => c.trim()));
          i++;
        } else {
          break;
        }
      }
      elements.push(
        <div key={`${keyPrefix}t${elemIdx++}`} className="my-3 overflow-x-auto">
          <table className="min-w-full text-xs border border-gray-200 rounded-lg overflow-hidden">
            <thead className="bg-gray-50">
              <tr>
                {headers.map((h, j) => (
                  <th key={j} className="px-3 py-2 text-left font-semibold text-gray-700 border-b border-gray-200">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, j) => (
                <tr key={j} className={j % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                  {row.map((cell, k) => (
                    <td key={k} className="px-3 py-2 text-gray-700 border-b border-gray-100">{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    } else {
      plainBuf.push(lines[i]);
      i++;
    }
  }
  flushPlain();
  return elements;
}

function renderContent(text) {
  // Model output almost always ends with a newline; the plain-text branch below
  // turns it into a <br /> and the bubble grows an empty row under the last
  // sentence. Drop it here, at the point of display, so the stored transcript
  // keeps what the model actually produced.
  const parts = trimBubbleText(text).split(/(```[\s\S]*?```)/g);
  return parts.map((part, i) => {
    if (part.startsWith('```')) {
      const inner = part.slice(3, -3);
      const newline = inner.indexOf('\n');
      const lang = newline > 0 ? inner.slice(0, newline).trim() : '';
      const code = newline > 0 ? inner.slice(newline + 1) : inner;
      return (
        <div key={i} className="relative my-3">
          {lang && (
            <div className="bg-gray-800 text-gray-400 text-xs px-4 py-1.5 rounded-t-lg border-b border-gray-700">
              {lang}
            </div>
          )}
          <pre
            className={`bg-gray-900 text-gray-100 text-xs p-4 overflow-x-auto ${lang ? 'rounded-b-lg' : 'rounded-lg'} whitespace-pre`}
          >
            <CopyButton text={code} />
            {code}
          </pre>
        </div>
      );
    }
    // For non-code parts: handle tables and inline code
    return <React.Fragment key={i}>{renderTableAwareText(part, `${i}-`)}</React.Fragment>;
  });
}

// ---------------------------------------------------------------------------
// Reasoning steps — think / plan calls rendered inline in execution order
// ---------------------------------------------------------------------------

// The reasoning tools (think/plan) are pass-through scratchpads, but the agent
// framework delivers their argument as a stringified payload — sometimes plain
// text, sometimes a JSON object like {"thought": "..."} or {"plan": "..."}.
// Extract the human-readable text so we never show raw JSON to the user.
function parseReasoningContent(raw) {
  if (raw == null) return '';
  const text = typeof raw === 'string' ? raw : String(raw);
  const trimmed = text.trim();
  if (!(trimmed.startsWith('{') || trimmed.startsWith('['))) return text;
  try {
    const obj = JSON.parse(trimmed);
    if (typeof obj === 'string') return obj;
    if (obj && typeof obj === 'object') {
      // Prefer the known field names, then fall back to the first string value.
      for (const key of ['thought', 'plan', 'content', 'text', 'input']) {
        if (typeof obj[key] === 'string') return obj[key];
      }
      const firstStr = Object.values(obj).find((v) => typeof v === 'string');
      if (firstStr != null) return firstStr;
    }
  } catch {
    // Not valid JSON — fall through and show the original text.
  }
  return text;
}

// Full static class strings per accent — Tailwind cannot resolve interpolated
// class names, so each variant must appear literally.
const REASONING_META = {
  think: {
    label: 'Thought',
    Icon: BrainCircuit,
    box: 'bg-violet-50/50 border-violet-100',
    icon: 'text-violet-600',
    title: 'text-violet-700',
  },
  plan: {
    label: 'Plan',
    Icon: ListChecks,
    box: 'bg-indigo-50/50 border-indigo-100',
    icon: 'text-indigo-600',
    title: 'text-indigo-700',
  },
};

function ReasoningStep({ step, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const meta = REASONING_META[step.kind] || REASONING_META.think;
  const { Icon, label } = meta;
  const content = parseReasoningContent(step.content);
  const preview = content.replace(/\s+/g, ' ').trim();
  return (
    <div className={`rounded-lg border ${meta.box}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-left"
      >
        <Icon className={`w-3.5 h-3.5 ${meta.icon} flex-shrink-0`} />
        <span className={`text-xs font-semibold ${meta.title} flex-shrink-0`}>{label}</span>
        {!open && (
          <span className="text-xs text-gray-400 truncate flex-1">{preview}</span>
        )}
        {open ? (
          <ChevronUp className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" />
        ) : (
          <ChevronDown className="w-3 h-3 text-gray-400 flex-shrink-0" />
        )}
      </button>
      {open && (
        <div className="px-3 pb-3 -mt-0.5 text-xs text-gray-700 whitespace-pre-wrap leading-relaxed">
          {content}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Message bubble
// ---------------------------------------------------------------------------
// Interactive buttons from a structured AgentResponse (kind "buttons" or the
// Telegram-native "telegram"). Clicking a button either opens its url or sends
// its value back as the user's next message via onAction. Unknown kinds render
// nothing here — the bubble's fallback_text/content already covers them.
function ResponseButtons({ response, onAction, disabled }) {
  if (!response) return null;
  const { kind } = response;

  let rows = [];
  if (kind === 'buttons') {
    const cols = Math.max(1, response.columns || 1);
    const items = response.buttons || [];
    for (let i = 0; i < items.length; i += cols) rows.push(items.slice(i, i + cols));
  } else if (kind === 'telegram') {
    rows = response.inline_keyboard || [];
  } else {
    return null;
  }
  if (!rows.length) return null;

  const onClick = (b) => {
    if (disabled) return;
    if (b.url) { window.open(b.url, '_blank', 'noopener,noreferrer'); return; }
    const value = b.value != null ? b.value : (b.label || b.text || '');
    if (value && onAction) onAction(String(value));
  };

  return (
    <div className="mt-2 flex flex-col gap-1.5">
      {rows.map((row, ri) => (
        <div key={ri} className="flex flex-wrap gap-1.5">
          {(row || []).map((b, bi) => (
            <button
              key={bi}
              type="button"
              disabled={disabled}
              onClick={() => onClick(b)}
              className="px-3 py-1.5 text-sm rounded-lg border border-indigo-200 bg-indigo-50
                text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 disabled:cursor-not-allowed
                transition-colors"
            >
              {b.label || b.text || (b.url ? 'Open' : '')}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

// The model's reasoning as it is being written — a three-line ticker under the
// "working" indicator, always showing the tail of the thought. Native reasoning
// only becomes a `think` step once it is complete (the `</think>` closes, or the
// answer starts), which on a long thought leaves the bubble blank for many
// seconds; the `think_delta` stream fills that gap. When the thought completes,
// the caller clears `thinking_live` and the collapsible ReasoningStep takes over.
//
// The tail is shown without JS scrolling: a fixed-height clipped box whose flex
// content is bottom-aligned overflows past its own top edge, so the newest lines
// stay in view.
function LiveThoughts({ text }) {
  if (!text || !text.trim()) return null;
  return (
    <div className="mt-2 flex items-start gap-1.5">
      <BrainCircuit className="w-3.5 h-3.5 text-violet-500 flex-shrink-0 mt-0.5 animate-pulse" />
      <div className="flex-1 min-w-0 h-[3.75rem] overflow-hidden flex flex-col justify-end">
        <div className="text-[11px] leading-5 text-gray-400 whitespace-pre-wrap break-words">
          {text}
        </div>
      </div>
    </div>
  );
}

// Files the agent created, edited or deleted during this turn, listed inside the
// reply itself. The Artifacts column only exists in Build view, so without this
// a chat-view user never sees that the agent touched the filesystem. Diffs are
// not persisted with the message (localStorage quota), so a reloaded
// conversation shows the file list and its +/- counts; the diff body is filled
// in from `artifactsByPath` while the session that produced it is still open.
function MessageFiles({ files, artifactsByPath }) {
  const items = files || [];
  // null = follow the default (expanded for a small change set). The list starts
  // empty and fills in as `artifact` events stream, so the default has to be
  // re-evaluated on every render, not captured as the initial state.
  const [userOpen, setUserOpen] = useState(null);
  const open = userOpen === null ? items.length <= 3 : userOpen;
  const totals = useMemo(() => {
    let add = 0, del = 0;
    for (const f of (files || [])) { add += f.additions || 0; del += f.deletions || 0; }
    return { add, del };
  }, [files]);
  if (!items.length) return null;
  return (
    <div className="mt-2 rounded-lg border border-gray-200 bg-gray-50/70 overflow-hidden">
      <button
        type="button"
        onClick={() => setUserOpen(!open)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-left hover:bg-gray-100/70"
      >
        <FileText className="w-3.5 h-3.5 text-gray-500 flex-shrink-0" />
        <span className="text-xs font-semibold text-gray-700 flex-shrink-0">
          {items.length} file{items.length === 1 ? '' : 's'} changed
        </span>
        <span className="flex items-center gap-1.5 text-[10px] font-mono flex-shrink-0">
          {totals.add > 0 && <span className="text-emerald-600">+{totals.add}</span>}
          {totals.del > 0 && <span className="text-red-600">−{totals.del}</span>}
        </span>
        {open
          ? <ChevronUp className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" />
          : <ChevronDown className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" />}
      </button>
      {open && (
        <div className="border-t border-gray-100 p-2 space-y-1.5">
          {items.map((f) => (
            <ArtifactItem
              key={f.path}
              artifact={{ ...f, ...(artifactsByPath?.[f.path] || {}) }}
              defaultOpen={false}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// Service entities the agent touched during this turn — a task it created, a
// view it edited, a file it wrote, a job it scheduled. Each one has a page in
// the dashboard that the reply text alone gives no way to reach, so the backend
// resolves them into link payloads (see common/entity_links.py) and they are
// rendered here as chips under the reply.
function MessageEntities({ entities }) {
  const { t } = useI18n();
  const items = entities || [];
  if (!items.length) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wide text-gray-400">
        <Link2 className="w-3 h-3" />
        {t('chat.entitiesTouched')}
      </span>
      {items.map((e) => {
        // The payload carries English noun/action for text-only surfaces
        // (Telegram); the UI prefers localized wording and falls back to those.
        const noun = t(`chat.entityKind.${e.kind}`, { defaultValue: e.noun || e.kind });
        const action = t(`chat.entityAction.${e.action}`, { defaultValue: e.action });
        const external = /^https?:\/\//i.test(e.url || '');
        const inner = (
          <>
            <span aria-hidden="true">{e.icon}</span>
            <span className="truncate max-w-[14rem] font-medium">{e.title}</span>
            <span className="text-gray-400 flex-shrink-0">{action}</span>
            <ArrowUpRight className="w-3 h-3 flex-shrink-0 text-gray-400" />
          </>
        );
        const cls = 'inline-flex items-center gap-1 max-w-full rounded-full border border-gray-200 '
          + 'bg-gray-50 px-2 py-0.5 text-[11px] text-gray-600 transition-colors '
          + 'hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700';
        return external ? (
          <a key={`${e.kind}:${e.id}`} href={e.url} title={noun} target="_blank" rel="noreferrer noopener" className={cls}>
            {inner}
          </a>
        ) : (
          <Link key={`${e.kind}:${e.id}`} to={e.url} title={noun} className={cls}>
            {inner}
          </Link>
        );
      })}
    </div>
  );
}

function MessageBubble({ msg, isStreaming = false, agentName, onAction, artifactsByPath }) {
  const { t } = useI18n();
  const isUser = msg.role === 'user';
  // A tool is currently executing (set on tool_start, cleared on the first
  // response token). Surfaced as a labelled indicator so the user sees that
  // memory tools (recall / remember / forget / …) are processing.
  const runningTool = !isUser && isStreaming ? msg.running_tool : null;
  const showTypingDots = !isUser && isStreaming && !msg.content;
  return (
    <div className={`flex gap-3 mb-6 mx-2 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      {/* Avatar */}
      <div className="flex flex-col items-center gap-1 flex-shrink-0">
        <div
          className={`w-8 h-8 rounded-full flex items-center justify-center text-white
            ${isUser ? 'bg-indigo-600' : 'bg-gray-800'}`}
        >
          {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
        </div>
        {!isUser && agentName && (
          <span className="text-[9px] text-gray-400 font-medium text-center leading-tight max-w-[56px] break-words">
            {agentName}
          </span>
        )}
      </div>

      {/* Bubble */}
      <div
        className={`max-w-[72%] text-base leading-relaxed
          ${isUser
            ? 'bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-4 py-3'
            : 'bg-white border border-gray-200 text-gray-800 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm'
          }
          ${msg.error ? 'border-red-300 bg-red-50 text-red-700' : ''}`}
      >
        {/* Thoughts, memory tool cards and delegated sub-agent runs, inline in
            the order they happened, above the response that followed them. */}
        {!isUser && <ChatTrail msg={msg} />}
        {showTypingDots ? (
          <span className="flex items-center gap-2">
            <span className="text-xs text-gray-400">{runningTool ? t('chat.runningTool', { tool: runningTool }) : t('chat.workingLabel')}</span>
            <span className="flex gap-1">
              {[0, 150, 300].map((delay) => (
                <span
                  key={delay}
                  className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
                  style={{ animationDelay: `${delay}ms` }}
                />
              ))}
            </span>
          </span>
        ) : isUser
          ? <span className="whitespace-pre-wrap">{trimBubbleText(msg.content)}</span>
          : <div>{renderContent(msg.content)}</div>
        }
        {/* The thought currently being written, tailing three lines. Cleared as
            soon as the completed thought arrives as a ReasoningStep above.
            Gated on the buffer rather than `isStreaming` so agent-initiated
            continuation runs (session SSE, no page-level loading flag) show it. */}
        {!isUser && <LiveThoughts text={msg.thinking_live} />}
        {/* Files the agent created / edited / deleted during this turn. */}
        {!isUser && <MessageFiles files={msg.files} artifactsByPath={artifactsByPath} />}
        {/* Links to the tasks / views / flows / files this turn touched. */}
        {!isUser && !showTypingDots && <MessageEntities entities={msg.entities} />}
        {/* Structured response UI (buttons / Telegram keyboard) under the text. */}
        {!isUser && !showTypingDots && (
          <ResponseButtons response={msg.response_obj} onAction={onAction} disabled={isStreaming} />
        )}
        {/* A rich view (chart / table / diagram / …) referenced by the reply. */}
        {!isUser && !showTypingDots && msg.response_obj?.kind === 'view_ref' && (
          <ViewCard viewRef={msg.response_obj} />
        )}
        {/* A tool started after some text already streamed (content present, so the
            typing-dots block above is hidden) — show a compact running indicator. */}
        {!isUser && runningTool && msg.content ? (
          <span className="mt-1 flex items-center gap-2 text-xs text-gray-400">
            <span>{t('chat.runningTool', { tool: runningTool })}</span>
            <span className="flex gap-1">
              {[0, 150, 300].map((delay) => (
                <span
                  key={delay}
                  className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
                  style={{ animationDelay: `${delay}ms` }}
                />
              ))}
            </span>
          </span>
        ) : null}
        {!isUser && msg.run_id && (
          <div className="mt-2 pt-2 border-t border-gray-100">
            <div className="flex items-center gap-1.5 mb-2">
              {typeof msg.inbound_tokens === 'number' && <TokenPill label={t('chat.in')} value={msg.inbound_tokens} />}
              {typeof msg.outbound_tokens === 'number' && <TokenPill label={t('chat.out')} value={msg.outbound_tokens} />}
              {typeof msg.duration_ms === 'number' && <TokenPill label={t('chat.duration')} value={fmtDurationMs(msg.duration_ms)} />}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Typing indicator
// ---------------------------------------------------------------------------
function TypingIndicator({ agentName }) {
  const { t } = useI18n();
  return (
    <div className="flex gap-3 mb-6">
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gray-800 flex items-center justify-center">
        <Bot className="w-4 h-4 text-white" />
      </div>
      <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm shadow-sm px-4 py-3 flex items-center gap-2">
        <span className="text-xs text-gray-400">{t('chat.agentIsWorking', { agent: agentName })}</span>
        <span className="flex gap-1">
          {[0, 150, 300].map((delay) => (
            <span
              key={delay}
              className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
              style={{ animationDelay: `${delay}ms` }}
            />
          ))}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent selector dropdown
// ---------------------------------------------------------------------------
function AgentDropdown({ agents, value, onChange }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const selected = agents.find((a) => a.id === value);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-sm text-gray-700 hover:bg-gray-50 transition-colors"
      >
        <Bot className="w-4 h-4 text-indigo-500" />
        <span className="font-medium">{selected?.name || t('chat.selectAgent')}</span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 bg-white border border-gray-200 rounded-xl shadow-lg z-50 min-w-[220px] py-1 max-h-64 overflow-y-auto">
          {agents.map((a) => (
            <button
              key={a.id}
              onClick={() => { onChange(a.id); setOpen(false); }}
              className={`w-full text-left px-4 py-2.5 text-sm hover:bg-indigo-50 transition-colors
                ${a.id === value ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-700'}`}
            >
              <div className="font-medium">{a.name}</div>
              {a.domain && (
                <div className="text-xs text-gray-400 mt-0.5">{a.domain}</div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** The selected target id for a mode — the single answer to "can we send?". */
function targetId(mode, { selectedAgent, selectedFlow, selectedTeam }) {
  if (mode === 'flow') return selectedFlow;
  if (mode === 'team') return selectedTeam;
  return selectedAgent;
}

function TeamDropdown({ teams, value, onChange }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const selected = teams.find((t) => t.team_id === value);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-sm text-gray-700 hover:bg-gray-50 transition-colors"
      >
        <UsersRound className="w-4 h-4 text-amber-500" />
        <span className="font-medium">{selected?.name || t('chat.selectTeam')}</span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 bg-white border border-gray-200 rounded-xl shadow-lg z-50 min-w-[280px] py-1 max-h-64 overflow-y-auto">
          {teams.length === 0 && (
            <div className="px-4 py-2 text-xs text-gray-400">{t('chat.noTeamsDefined')}</div>
          )}
          {teams.map((t) => (
            <button
              key={t.team_id}
              onClick={() => { onChange(t.team_id); setOpen(false); }}
              className={`w-full text-left px-4 py-2.5 text-sm hover:bg-amber-50 transition-colors
                ${t.team_id === value ? 'bg-amber-50 text-amber-700 font-medium' : 'text-gray-700'}`}
            >
              <div className="font-medium truncate">{t.name}</div>
              <div className="text-xs text-gray-400 mt-0.5">
                {t.mode} · {(t.members || []).length} member{(t.members || []).length === 1 ? '' : 's'}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function FlowDropdown({ flows, value, onChange }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const selected = flows.find((f) => f.id === value);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-sm text-gray-700 hover:bg-gray-50 transition-colors"
      >
        <Workflow className="w-4 h-4 text-emerald-500" />
        <span className="font-medium">{selected?.name || t('chat.selectFlow')}</span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 bg-white border border-gray-200 rounded-xl shadow-lg z-50 min-w-[260px] py-1 max-h-64 overflow-y-auto">
          {flows.length === 0 && (
            <div className="px-4 py-2 text-xs text-gray-400">{t('chat.noFlowsDefined')}</div>
          )}
          {flows.map((f) => {
            const nodeCount = (f.nodes || []).length;
            return (
              <button
                key={f.id}
                onClick={() => { onChange(f.id); setOpen(false); }}
                className={`w-full text-left px-4 py-2.5 text-sm hover:bg-emerald-50 transition-colors
                  ${f.id === value ? 'bg-emerald-50 text-emerald-700 font-medium' : 'text-gray-700'}`}
              >
                <div className="font-medium truncate">{f.name}</div>
                <div className="text-xs text-gray-400 mt-0.5">{t('chat.nodeCount', { count: nodeCount })}</div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ProcessPanelContent({ processInsights }) {
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
      <ProcessGraph key={graphKey} messageRuns={processInsights.message_runs || []} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Build view — unified diff renderer (no external deps)
// ---------------------------------------------------------------------------
function DiffView({ diff }) {
  const { t } = useI18n();
  if (!diff) {
    return <div className="px-3 py-2 text-[11px] text-gray-400 italic">{t('chat.noTextualDiffAvailable')}</div>;
  }
  const lines = diff.split('\n');
  return (
    <div className="font-mono text-[11px] leading-relaxed overflow-x-auto">
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
            <div className="px-3 py-2 text-[11px] text-gray-400 italic">
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

function ArtifactsPanel({ artifacts }) {
  const { t } = useI18n();
  const items = useMemo(
    () => Object.values(artifacts || {}).sort((a, b) => (a.path || '').localeCompare(b.path || '')),
    [artifacts],
  );
  const totals = useMemo(() => {
    let add = 0, del = 0;
    for (const a of items) { add += a.additions || 0; del += a.deletions || 0; }
    return { add, del };
  }, [items]);

  if (!items.length) {
    return (
      <div className="flex-1 overflow-y-auto p-4">
        <p className="text-xs text-gray-500 italic">
          {t('chat.noFilesChangedYetWhen')}
        </p>
      </div>
    );
  }
  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-2">
      <div className="flex items-center gap-2 pb-1 text-[11px] text-gray-500">
        <span className="font-semibold text-gray-600">{t('chat.fileCount', { count: items.length })}</span>
        <span className="font-mono text-emerald-600">+{totals.add}</span>
        <span className="font-mono text-red-600">−{totals.del}</span>
      </div>
      {items.map((a) => (
        <ArtifactItem key={a.path} artifact={a} defaultOpen={items.length <= 2} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Build view — full inline transcript (messages + thinking/plan + tools + artifacts)
// ---------------------------------------------------------------------------
function TimelineToolCard({ entry }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const isSkill = entry.tool === SKILL_TOOL;
  return (
    <div className={`rounded-lg border ${isSkill ? 'border-violet-200 bg-violet-50/50' : 'border-amber-200 bg-amber-50/50'}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-left"
      >
        {isSkill ? <Zap className="w-3.5 h-3.5 text-violet-500 flex-shrink-0" /> : <Terminal className="w-3.5 h-3.5 text-amber-600 flex-shrink-0" />}
        <span className={`text-xs font-semibold ${isSkill ? 'text-violet-700' : 'text-amber-700'}`}>{entry.tool || 'tool'}</span>
        {entry.running && (
          <span className="flex gap-1 ml-1">
            {[0, 150, 300].map((d) => (
              <span key={d} className="w-1 h-1 bg-amber-400 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />
            ))}
          </span>
        )}
        {!open && entry.input && (
          <span className="text-[11px] text-gray-400 truncate flex-1">{shortText(entry.input, 80)}</span>
        )}
        {open ? <ChevronUp className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" /> : <ChevronDown className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" />}
      </button>
      {open && (
        <div className="px-3 pb-2.5 space-y-1.5">
          {entry.input && (
            <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-all">
              <span className="text-gray-400">{t('chat.in2')}</span> {shortText(entry.input, 1000)}
            </div>
          )}
          {entry.output != null && (
            <div className="text-[11px] text-emerald-800 whitespace-pre-wrap break-all">
              <span className="text-emerald-600">{t('chat.out2')}</span> {shortText(entry.output, 1000)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Delegation (run_agent_tool) live-nesting
// ---------------------------------------------------------------------------
// A delegated child run streams its tool/thought events tagged with the child
// run_id + nesting depth. The event handler folds them into a `delegation`
// timeline entry: { type:'delegation', run_id, agent_id, agent_name, input,
// running, ok, timeline:[ ...nested entries... ] }. Nested entries use the same
// shapes as top-level ones (reasoning / tool / delegation), so a delegation can
// itself contain deeper delegations — rendered recursively by DelegationCard.

// Apply `fn` to the delegation entry whose run_id matches, searching nested
// delegation timelines. Returns a new timeline array (immutable), or the same
// reference when nothing matched (so React can skip untouched branches).
function mapDelegation(timeline, runId, fn) {
  if (!Array.isArray(timeline)) return timeline;
  let changed = false;
  const next = timeline.map((e) => {
    if (e && e.type === 'delegation') {
      if (e.run_id === runId) { changed = true; return fn(e); }
      const inner = mapDelegation(e.timeline || [], runId, fn);
      if (inner !== (e.timeline || [])) { changed = true; return { ...e, timeline: inner }; }
    }
    return e;
  });
  return changed ? next : timeline;
}

// Append `entry` to the delegation(parentRunId)'s nested timeline, or to the
// top-level timeline when parentRunId is absent or is the message's own run.
function appendUnderDelegation(timeline, parentRunId, messageRunId, entry) {
  if (!parentRunId || parentRunId === messageRunId) {
    return [...(timeline || []), entry];
  }
  return mapDelegation(timeline, parentRunId, (d) => ({
    ...d, timeline: [...(d.timeline || []), entry],
  }));
}

// Append an inner entry (reasoning / tool) into delegation(runId)'s own timeline.
function appendIntoDelegation(timeline, runId, entry) {
  return mapDelegation(timeline, runId, (d) => ({
    ...d, timeline: [...(d.timeline || []), entry],
  }));
}

// Resolve the last still-running tool inside delegation(runId) with `patch`.
function resolveDelegationTool(timeline, runId, patch) {
  return mapDelegation(timeline, runId, (d) => {
    const tl = [...(d.timeline || [])];
    for (let i = tl.length - 1; i >= 0; i -= 1) {
      if (tl[i].type === 'tool' && tl[i].running) { tl[i] = { ...tl[i], ...patch, running: false }; break; }
    }
    return { ...d, timeline: tl };
  });
}

// A live, collapsible block for one delegated agent run: header (agent + status)
// over its nested thoughts and tool calls, the same cards the parent uses.
// Recursive: a nested `delegation` entry renders another DelegationCard.
function DelegationCard({ entry }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  const nested = entry.timeline || [];
  const running = entry.running;
  const failed = entry.ok === false && !running;
  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-left"
      >
        <Repeat className="w-3.5 h-3.5 text-indigo-500 flex-shrink-0" />
        <span className="text-xs font-semibold text-indigo-700 flex-shrink-0">
          Delegated → {entry.agent_name || entry.agent_id}
        </span>
        {running ? (
          <span className="flex gap-1 ml-1">
            {[0, 150, 300].map((d) => (
              <span key={d} className="w-1 h-1 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: `${d}ms` }} />
            ))}
          </span>
        ) : (
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ml-1 ${failed ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'}`}>
            {failed ? 'failed' : 'done'}
          </span>
        )}
        {!open && entry.input && (
          <span className="text-[11px] text-gray-400 truncate flex-1">{shortText(entry.input, 60)}</span>
        )}
        {open ? <ChevronUp className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" /> : <ChevronDown className="w-3 h-3 text-gray-400 ml-auto flex-shrink-0" />}
      </button>
      {open && (
        <div className="px-3 pb-2.5 pt-0.5 ml-2 border-l-2 border-indigo-100 space-y-1.5">
          {nested.length === 0 ? (
            <div className="text-[11px] text-gray-400 italic">{t('chat.working')}</div>
          ) : (
            nested.map((e, i) => {
              if (e.type === 'reasoning') return <ReasoningStep key={i} step={e} />;
              if (e.type === 'delegation') return <DelegationCard key={i} entry={e} />;
              if (e.type === 'tool') {
                if (EXTRACTION_TOOLS.includes(e.tool)) return <ExtractionToolCard key={i} entry={e} />;
                if (e.tool === 'recall') return <RecallToolCard key={i} entry={e} />;
                return <TimelineToolCard key={i} entry={e} />;
              }
              if (e.type === 'text') {
                return e.text && e.text.trim()
                  ? <div key={i} className="text-[11px] text-gray-700 whitespace-pre-wrap break-words">{e.text}</div>
                  : null;
              }
              return null;
            })
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Extraction tool cards — rich rendering for extract_from_text / save_extraction
// ---------------------------------------------------------------------------
const EXTRACTION_TOOLS = ['extract_from_text', 'save_extraction'];

const EPISODE_KIND_CLS = {
  decision: 'bg-blue-100 text-blue-700',
  error: 'bg-red-100 text-red-700',
  task: 'bg-emerald-100 text-emerald-700',
  observation: 'bg-gray-100 text-gray-600',
  interaction: 'bg-gray-100 text-gray-600',
};

function parseJsonSafe(text) {
  try { return JSON.parse(text); } catch { return null; }
}

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

// The chat bubble's process trail: thoughts, the rich memory cards (recall /
// extraction) and delegated sub-agent runs. `timeline` is the chronological
// feed the Build view and the Process graph use, so walking it keeps every card
// in execution order instead of grouping all thoughts above all tools. Other
// tool calls stay Build-view only. `timeline` is transient (stripped before
// persisting), so a reloaded conversation falls back to the thoughts alone,
// which do persist on `reasoning`.
function ChatTrail({ msg }) {
  const timeline = msg.timeline || [];
  const entries = timeline.length
    ? timeline.filter((e) => (
        e.type === 'reasoning'
        || e.type === 'delegation'
        || (e.type === 'tool' && (EXTRACTION_TOOLS.includes(e.tool) || e.tool === 'recall'))
      ))
    : (msg.reasoning || []).map((s) => ({ type: 'reasoning', ...s }));
  if (!entries.length) return null;
  return (
    <div className="space-y-2 mb-2">
      {entries.map((e, i) => {
        if (e.type === 'reasoning') return <ReasoningStep key={i} step={e} />;
        if (e.type === 'delegation') return <DelegationCard key={e.run_id || i} entry={e} />;
        return e.tool === 'recall'
          ? <RecallToolCard key={i} entry={e} />
          : <ExtractionToolCard key={i} entry={e} />;
      })}
    </div>
  );
}

function TimelineArtifactChip({ entry, onJump }) {
  const meta = ARTIFACT_OP_META[entry.op] || ARTIFACT_OP_META.modify;
  return (
    <button
      type="button"
      onClick={() => onJump?.(entry.path)}
      className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 text-left max-w-full"
      title={`${entry.path} — jump to diff`}
    >
      <span className={`w-4 h-4 rounded flex items-center justify-center text-[9px] font-bold flex-shrink-0 ${meta.cls}`}>{meta.label}</span>
      <FileText className="w-3 h-3 text-gray-400 flex-shrink-0" />
      <span className="text-[11px] font-medium text-gray-700 truncate">{entry.path}</span>
      <span className="text-[10px] font-mono flex-shrink-0">
        {entry.additions > 0 && <span className="text-emerald-600">+{entry.additions}</span>}{' '}
        {entry.deletions > 0 && <span className="text-red-600">−{entry.deletions}</span>}
      </span>
    </button>
  );
}

function BuildMessage({ msg, agentName, onJumpArtifact }) {
  const { t } = useI18n();
  const isUser = msg.role === 'user';
  if (isUser) {
    return (
      <div className="flex gap-3 mb-5 mx-2 flex-row-reverse">
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-white">
          <User className="w-4 h-4" />
        </div>
        <div className="max-w-[72%] bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 text-base whitespace-pre-wrap">
          {trimBubbleText(msg.content)}
        </div>
      </div>
    );
  }

  // Agent message: render the chronological timeline. Fall back to plain content
  // for older messages that pre-date timeline capture (e.g. reloaded history).
  const timeline = msg.timeline && msg.timeline.length ? msg.timeline : null;
  return (
    <div className="flex gap-3 mb-5 mx-2">
      <div className="flex flex-col items-center gap-1 flex-shrink-0">
        <div className="w-8 h-8 rounded-full bg-gray-800 flex items-center justify-center text-white">
          <Bot className="w-4 h-4" />
        </div>
        {agentName && (
          <span className="text-[9px] text-gray-400 font-medium text-center leading-tight max-w-[56px] break-words">{agentName}</span>
        )}
      </div>
      <div className={`flex-1 min-w-0 space-y-2 ${msg.error ? 'text-red-700' : ''}`}>
        {timeline ? (
          timeline.map((entry, i) => {
            if (entry.type === 'text') {
              if (!entry.text || !entry.text.trim()) return null;
              return (
                <div key={i} className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm text-base text-gray-800 leading-relaxed">
                  {renderContent(entry.text)}
                </div>
              );
            }
            if (entry.type === 'reasoning') {
              return <ReasoningStep key={i} step={entry} />;
            }
            if (entry.type === 'delegation') {
              return <DelegationCard key={i} entry={entry} />;
            }
            if (entry.type === 'tool') {
              if (EXTRACTION_TOOLS.includes(entry.tool)) {
                return <ExtractionToolCard key={i} entry={entry} />;
              }
              if (entry.tool === 'recall') {
                return <RecallToolCard key={i} entry={entry} />;
              }
              return <TimelineToolCard key={i} entry={entry} />;
            }
            if (entry.type === 'artifact') {
              return <TimelineArtifactChip key={i} entry={entry} onJump={onJumpArtifact} />;
            }
            return null;
          })
        ) : (
          <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm text-base text-gray-800 leading-relaxed">
            {msg.content ? renderContent(msg.content) : <span className="text-gray-400 text-xs italic">{t('chat.noOutput')}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Chat page
// ---------------------------------------------------------------------------
export default function Chat() {
  const { t } = useI18n();
  const { convId: urlConvId } = useParams();
  const navigate = useNavigate();
  const { selectedWorkspace } = useWorkspace();
  // This tab's id on the shared SSE stream: sent with a turn so the
  // broadcast can name its author, and with a save for the same reason.
  const { clientId } = useStream();
  const [agents, setAgents] = useState([]);
  const [workspaceAllowedAgentIds, setWorkspaceAllowedAgentIds] = useState(null);
  const [selectedAgent, setSelectedAgent] = useState('');
  // Flow chat support: when targetMode === 'flow', messages run through the selected flow
  // (each user turn is processed by every node in the DAG in topological order).
  const [flows, setFlows] = useState([]);
  const [selectedFlow, setSelectedFlow] = useState('');
  // Team chat support: when targetMode === 'team', the message is handed to a
  // roster of agents that talk to each other; each board entry becomes a bubble.
  const [teams, setTeams] = useState([]);
  const [selectedTeam, setSelectedTeam] = useState('');
  const [targetMode, setTargetMode] = useState('agent'); // 'agent' | 'flow' | 'team'

  const [projects, setProjects] = useState([]);
  const [selectedProject, setSelectedProject] = useState('');

  const [currentConvId, setCurrentConvId] = useState(urlConvId || null);
  // Whether a turn is being sent from this tab. Declared here because both the
  // conversation store and the live-turn mirror below are steered by it.
  const [loading, setLoading] = useState(false);
  // The conversation list is server state, not browser state: the store loads
  // it, fetches the open chat's transcript, and writes changes back. What this
  // page sees is the array it has always mutated.
  // `paused` while a turn is running here: a reload triggered by someone else's
  // save would drop the turn this tab is in the middle of writing.
  const {
    conversations, setConversations, removeConversation, syncError,
  } = useConversationStore(currentConvId, { paused: loading });
  const [telegramBindings, setTelegramBindings] = useState([]);
  const [telegramSending, setTelegramSending] = useState(false);
  const [telegramError, setTelegramError] = useState('');

  const [input, setInput] = useState('');
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [attachmentError, setAttachmentError] = useState('');
  // Hub records attached to the next message — {kind, id, label, icon, url}.
  // Separate from pendingAttachments: a reference is a pointer the server
  // resolves at send time, not a payload the browser carries.
  const [pendingReferences, setPendingReferences] = useState([]);
  // The paperclip's menu (a file, or one of the entity kinds) and the kind the
  // picker modal is open on (null = closed).
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [pickerKind, setPickerKind] = useState(null);
  const [contextKinds, setContextKinds] = useState([]);
  const [processOpen, setProcessOpen] = useState(() => {
    try { return localStorage.getItem(PROCESS_OPEN_KEY) === '1'; } catch { return false; }
  });
  // 'chat' = clean message bubbles (default). 'build' = full inline transcript
  // (messages + thinking + plan + tool calls) with an Artifacts (diffs) column.
  const [viewMode, setViewMode] = useState(() => {
    try { return localStorage.getItem(VIEW_MODE_KEY) === 'build' ? 'build' : 'chat'; } catch { return 'chat'; }
  });
  // Latest cumulative diff per file path for the current conversation.
  // Shape: { [path]: { op, path, diff, additions, deletions, binary, truncated, run_id } }
  const [artifacts, setArtifacts] = useState({});
  const [activeRunId, setActiveRunId] = useState(null);
  const [processLoading, setProcessLoading] = useState(false);
  const [processError, setProcessError] = useState('');
  const [processInsights, setProcessInsights] = useState({
    messages: [],
    tools: [],
    thinking: [],
    message_runs: [],
    token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
    context_peak: 0,
    context_window_tokens: 0,
  });

  // Persist the process-panel open state so it survives leaving and returning to
  // the Chat page (the component unmounts on navigation, resetting React state).
  useEffect(() => {
    try { localStorage.setItem(PROCESS_OPEN_KEY, processOpen ? '1' : '0'); } catch { /* storage unavailable */ }
  }, [processOpen]);

  useEffect(() => {
    try { localStorage.setItem(VIEW_MODE_KEY, viewMode); } catch { /* storage unavailable */ }
  }, [viewMode]);

  const [commandMenuOpen, setCommandMenuOpen] = useState(false);
  const [commandMenuIndex, setCommandMenuIndex] = useState(0);

  // session_id from the backend — used to subscribe to continuation SSE
  const [sessionId, setSessionId] = useState(null);

  const abortCtrlRef = useRef(null);
  const continuationMsgIdRef = useRef(null);   // active continuation bubble on the session channel
  const messagesEndRef = useRef(null);
  const prevConvIdRef = useRef(undefined);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const loadedRunIdsRef = useRef(new Set());   // tracks which run_ids have been fetched
  const processInsightsRef = useRef(processInsights); // used inside loadProcessData to check if silent

  // ---- derived state ----
  // Top (normal) chat list: never show telegram-origin convs here — they live
  // in the dedicated Telegram panel below.
  const visibleConversations = useMemo(() => {
    const noTelegram = conversations.filter((c) => c.origin !== 'telegram');
    if (!selectedWorkspace || selectedWorkspace === 'default') return noTelegram;
    return noTelegram.filter((c) => c.workspace === selectedWorkspace);
  }, [conversations, selectedWorkspace]);

  // Strict workspace isolation: a Telegram binding is shown only when its
  // workspace exactly matches the active workspace. The `default` selection
  // matches only bindings that explicitly point at `default` (no fallback to
  // "show everything"), so each workspace owns its slice of the Telegram thread.
  const visibleTelegramBindings = useMemo(() => {
    if (!selectedWorkspace) return [];
    return telegramBindings.filter((b) => (b.workspace || null) === selectedWorkspace);
  }, [telegramBindings, selectedWorkspace]);

  const currentTelegramBinding = useMemo(() => {
    if (!currentConvId) return null;
    return telegramBindings.find((b) => b.conversation_id === currentConvId) || null;
  }, [telegramBindings, currentConvId]);

  const currentConv = conversations.find((c) => c.id === currentConvId) || null;
  // Memoised: the `|| []` fallback would otherwise be a new array on every
  // render, re-running every effect that watches the transcript.
  const messages = useMemo(() => currentConv?.messages || [], [currentConv]);
  // Context fill for the open conversation: whatever the most recent turn that
  // reported it left behind. Read off the transcript rather than tracked live,
  // so it is still right after a reload or a switch between conversations, and
  // empties by itself when /clear empties the messages.
  const contextUsage = useMemo(() => {
    const msgs = currentConv?.messages || [];
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      const m = msgs[i];
      if (m?.context_window || m?.context_overflow) {
        return {
          used: m.context_used || 0,
          window: m.context_window || 0,
          overflow: Boolean(m.context_overflow),
        };
      }
    }
    return { used: 0, window: 0, overflow: false };
  }, [currentConv]);
  const conversationRunIds = useMemo(() => {
    const ids = new Set();
    for (const m of (currentConv?.messages || [])) {
      if (m?.role === 'agent' && m?.run_id) ids.add(String(m.run_id));
    }
    return ids;
  }, [currentConv]);

  // A turn someone else is running in this same conversation — another tab,
  // another device, Telegram, an agent writing to its own inbox. It is mirrored
  // live and never saved: the tab that ran it writes the transcript, and this
  // mirror steps aside as soon as that lands (hence the run ids above).
  // `muted` while this tab is the one sending: it renders its own stream and
  // would otherwise draw every token twice.
  const liveTurn = useLiveChatTurn(currentConvId, {
    muted: loading,
    resolvedRunIds: conversationRunIds,
  });
  const liveMessages = useMemo(() => {
    if (!liveTurn) return [];
    const bubbles = [];
    if ((liveTurn.user || '').trim()) {
      bubbles.push({ id: 'live-user', role: 'user', content: liveTurn.user });
    }
    bubbles.push({
      id: 'live-agent',
      role: 'agent',
      agent_id: liveTurn.agentId || selectedAgent,
      content: liveTurn.text,
      reasoning: liveTurn.thinking,
      thinking_live: liveTurn.thinkingLive,
      running_tool: liveTurn.tools.find((x) => x.output === null && x.error === null)?.tool || null,
      error: liveTurn.status === 'failed',
      run_id: liveTurn.runId,
    });
    return bubbles;
  }, [liveTurn, selectedAgent]);
  // What the feed renders: the stored transcript, plus the mirrored turn while
  // one is in flight. The mirror is appended here and nowhere else, so nothing
  // downstream of `messages` (persistence, context fill, run ids) ever sees it.
  const renderedMessages = useMemo(
    () => (liveMessages.length ? [...messages, ...liveMessages] : messages),
    [messages, liveMessages],
  );
  // Build-view timelines for reloaded messages. The live `msg.timeline` (tool
  // calls + thoughts) is not stored with the chat (TRANSIENT_MSG_FIELDS in
  // components/chatStore.js), so a conversation reopened from the store has none. Reconstruct it per run_id from
  // the server-fetched process insights — same reasoning+tools merge the Process
  // graph uses — so the Build view shows tools and thoughts again, not just the
  // final text. Keyed by run_id; consumed only in build view.
  const runTimelineByRunId = useMemo(() => {
    const map = {};
    for (const mr of (processInsights.message_runs || [])) {
      const rid = String(mr?.run_id || '');
      if (!rid) continue;
      const merged = [
        ...((mr.reasoning || []).map((r) => ({ step: r.step, entry: { type: 'reasoning', kind: r.kind || 'think', step: r.step, content: r.content } }))),
        ...((mr.tools || []).map((t) => ({ step: t.step, entry: { type: 'tool', step: t.step, tool: t.tool, input: t.input, output: t.output } }))),
      ].sort((a, b) => (a.step ?? Infinity) - (b.step ?? Infinity));
      const timeline = merged.map((m) => m.entry);
      // The final response text follows the tool/thought steps.
      if ((mr.output || '').trim()) timeline.push({ type: 'text', text: mr.output });
      map[rid] = timeline;
    }
    return map;
  }, [processInsights]);

  const agentName = agents.find((a) => a.id === selectedAgent)?.name || selectedAgent || 'Agent';
  // Whether the composer has something to send to, and what it says while it
  // waits. Derived once for all three target modes so a new mode cannot be
  // added to one control and forgotten in the next.
  const hasTarget = Boolean(targetId(targetMode, { selectedAgent, selectedFlow, selectedTeam }));
  const composerPlaceholder = (() => {
    if (targetMode === 'flow') {
      return selectedFlow
        ? t('chat.placeholders.flow', { name: flows.find((f) => f.id === selectedFlow)?.name || '' })
        : t('chat.placeholders.pickFlow');
    }
    if (targetMode === 'team') {
      return selectedTeam
        ? t('chat.placeholders.team', { name: teams.find((tm) => tm.team_id === selectedTeam)?.name || '' })
        : t('chat.placeholders.pickTeam');
    }
    return selectedAgent
      ? t('chat.placeholders.agent', { name: agents.find((a) => a.id === selectedAgent)?.name || selectedAgent })
      : t('chat.placeholders.noAgent');
  })();
  const _agentObj = agents.find((a) => a.id === selectedAgent) || {};
  // provider/model are top-level fields on AgentSpec
  const agentProvider = _agentObj.provider || 'inherit';
  const agentModel = _agentObj.model || '';
  const selectableAgents = useMemo(() => {
    if (!selectedWorkspace) return agents;
    const allowedSet = new Set(workspaceAllowedAgentIds || []);
    return agents.filter((a) => allowedSet.has(a.id));
  }, [agents, selectedWorkspace, workspaceAllowedAgentIds]);

  const allCommands = useMemo(() => {
    const agentCmds = agents.find((a) => a.id === selectedAgent)?.commands || [];
    return [...GLOBAL_COMMANDS, ...agentCmds];
  }, [agents, selectedAgent]);

  const commandSuggestions = useMemo(() => {
    if (!commandMenuOpen) return [];
    const query = input.toLowerCase();
    return allCommands.filter((cmd) => cmd.name.toLowerCase().startsWith(query));
  }, [commandMenuOpen, input, allCommands]);

  // ---- sync URL → state ----
  useEffect(() => {
    setCurrentConvId(urlConvId || null);
  }, [urlConvId]);

  // ---- close the active conversation when its workspace doesn't match ----
  // Each workspace owns its own chat history; switching workspace should drop
  // the current conv and land on the start page rather than show a chat that
  // belongs to a different workspace.
  useEffect(() => {
    if (!currentConvId) return;
    if (!selectedWorkspace) return;
    // The Telegram binding takes priority — it carries the authoritative workspace
    // for any conv promoted from a binding (the local conv may not exist yet).
    const tgBinding = telegramBindings.find((b) => b.conversation_id === currentConvId);
    if (tgBinding) {
      if ((tgBinding.workspace || null) !== selectedWorkspace) {
        navigate('/chat');
      }
      return;
    }
    const conv = conversations.find((c) => c.id === currentConvId);
    if (!conv) return;
    const convWs = conv.workspace || null;
    // For non-Telegram conversations, the `default` selection means "no filter",
    // matching the current visibleConversations behaviour.
    if (selectedWorkspace === 'default') return;
    if (convWs !== selectedWorkspace) {
      navigate('/chat');
    }
  }, [selectedWorkspace, currentConvId, telegramBindings, conversations, navigate]);

  // Persistence lives in useConversationStore: changes to `conversations` are
  // debounced into `PUT /api/chats/{id}` rather than rewritten into localStorage.

  // ---- load agents ----
  useEffect(() => {
    getAgents(selectedWorkspace || 'default')
      .then((r) => { setAgents(r.data || []); })
      .catch(() => {});
  }, [selectedWorkspace]);

  // ---- load flows (re-runs when workspace changes so we see workspace-bound flows) ----
  useEffect(() => {
    listFlows(selectedWorkspace || undefined)
      .then((r) => setFlows(r.data || []))
      .catch(() => setFlows([]));
  }, [selectedWorkspace]);

  // ---- load teams (workspace-scoped, same as flows) ----
  useEffect(() => {
    getTeams(selectedWorkspace || undefined)
      .then((r) => setTeams(r.data?.teams || []))
      .catch(() => setTeams([]));
  }, [selectedWorkspace]);

  // ---- load Telegram bindings (refresh on workspace switch + interval) ----
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const { data } = await getTelegramBindings();
        if (!cancelled) setTelegramBindings(data || []);
      } catch {
        if (!cancelled) setTelegramBindings([]);
      }
    };
    refresh();
    return () => { cancelled = true; };
  }, []);

  // ---- hydrate Telegram conversation: resolve session_id, load past messages ----
  // Re-runs whenever the binding *or its workspace* changes. The Telegram thread
  // is one conversation_id across workspaces, but each workspace shows only its
  // own slice of runs — so when the workspace context flips, we re-fetch and
  // replace the bubble list with the slice that belongs to the new workspace.
  // Past bubbles are reconstructed from server-side run logs via getMessageInsights.
  const bindingWorkspaceKey = currentTelegramBinding?.workspace || null;
  useEffect(() => {
    if (!currentTelegramBinding) return;
    const convId = currentTelegramBinding.conversation_id;
    if (!convId) return;
    let cancelled = false;

    (async () => {
      try {
        const { data: sessions } = await getSessions({ conversation_id: convId, limit: 1 });
        const sess = (sessions?.items || [])[0];
        if (!sess?.session_id || cancelled) return;
        setSessionId(sess.session_id);

        const { data: runs } = await getSessionMessages(sess.session_id);
        // Filter runs to only those that executed against the binding's workspace.
        const bindingWorkspace = currentTelegramBinding.workspace || null;
        const scopedRuns = (runs || []).filter((r) => (r.workspace || null) === bindingWorkspace);
        const insightsList = await Promise.all(
          scopedRuns.map((r) =>
            getMessageInsights(r.run_id).then((res) => ({ run: r, insights: res.data }))
              .catch(() => null)
          )
        );
        if (cancelled) return;

        const bubbles = [];
        for (const entry of insightsList) {
          if (!entry) continue;
          const runId = entry.run.run_id;
          const agentId = entry.run.agent_id;
          const msgs = entry.insights?.messages || [];
          for (const m of msgs) {
            bubbles.push({
              id: genId(),
              role: m.role === 'assistant' ? 'agent' : 'user',
              content: m.content || '',
              agent_id: m.role === 'assistant' ? agentId : undefined,
              run_id: m.role === 'assistant' ? runId : undefined,
              origin: 'telegram',
            });
          }
        }

        setConversations((prev) =>
          prev.map((c) => (c.id !== convId ? c : { ...c, messages: bubbles }))
        );
      } catch {
        // Best-effort hydration; failures are silent.
      }
    })();

    return () => { cancelled = true; };
  }, [currentTelegramBinding, bindingWorkspaceKey, setConversations]);


  // ---- load projects for selected workspace ----
  useEffect(() => {
    if (!selectedWorkspace) { setProjects([]); setSelectedProject(''); return; }
    getProjects(selectedWorkspace)
      .then((r) => setProjects(r.data || []))
      .catch(() => setProjects([]));
  }, [selectedWorkspace]);

  // ---- load allowed agents for selected workspace ----
  useEffect(() => {
    if (!selectedWorkspace) {
      setWorkspaceAllowedAgentIds(null);
      return;
    }
    getWorkspace(selectedWorkspace)
      .then((r) => {
        setWorkspaceAllowedAgentIds(r.data?.metadata?.allowed_agents || []);
      })
      .catch(() => {
        setWorkspaceAllowedAgentIds([]);
      });
  }, [selectedWorkspace]);

  // ---- ensure selected agent is valid for current workspace ----
  useEffect(() => {
    if (!selectableAgents.length) {
      if (selectedAgent) setSelectedAgent('');
      return;
    }
    const stillValid = selectableAgents.some((a) => a.id === selectedAgent);
    if (!stillValid) {
      const defaultAgent = selectableAgents.find((a) => a.is_default_chat_agent);
      setSelectedAgent((defaultAgent || selectableAgents[0]).id);
    }
  }, [selectableAgents, selectedAgent]);

  // ---- focus textarea on mount and whenever loading ends ----
  useEffect(() => {
    textareaRef.current?.focus();
  }, []);  // mount only

  useEffect(() => {
    if (!loading) {
      // Defer by one tick so React finishes re-enabling the textarea first
      const t = setTimeout(() => textareaRef.current?.focus(), 0);
      return () => clearTimeout(t);
    }
  }, [loading]);

  // ---- auto-scroll ----
  useEffect(() => {
    const isSwitching = prevConvIdRef.current !== currentConvId;
    prevConvIdRef.current = currentConvId;
    messagesEndRef.current?.scrollIntoView({ behavior: isSwitching ? 'instant' : 'smooth' });
  }, [messages, loading, currentConvId]);

  // Select latest run when switching conversations
  useEffect(() => {
    if (!currentConv) {
      setActiveRunId(null);
      return;
    }
    const latestRunMsg = [...(currentConv.messages || [])]
      .reverse()
      .find((m) => m.role === 'agent' && m.run_id);
    setActiveRunId(latestRunMsg?.run_id || null);
  }, [currentConvId, currentConv]);

  // ---- session SSE subscription for continuation runs ----
  // Subscribe to this session's channel on the shared multiplexed stream so
  // continuation runs (spawned as subprocesses after the original HTTP response
  // closed) stream their output into this chat in real time. No dedicated
  // connection — interest is added/removed on the single app-wide EventSource.
  useEffect(() => { continuationMsgIdRef.current = null; }, [sessionId, currentConvId]);

  useChannel(sessionId && currentConvId ? sessionId : null, (event) => {
    if (!event || !event.type) return;

    // Ignore heartbeats and events from the primary run (handled by the fetch stream)
    if (event.type === 'heartbeat') return;
    if (event.type === 'meta' && !event.continuation) return;

    const convId = currentConvId;

    if (event.type === 'meta' && event.continuation) {
      // A new continuation run is starting — create a new assistant message bubble
      const continuationMsgId = genId();
      continuationMsgIdRef.current = continuationMsgId;
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: [
              ...c.messages,
              {
                id: continuationMsgId,
                role: 'agent',
                agent_id: event.agent_id || '',
                content: '',
                error: false,
                run_id: event.run_id || null,
                inbound_tokens: null,
                outbound_tokens: null,
                total_tokens: null,
                tool_calls: null,
                duration_ms: null,
              },
            ],
          }
        )
      );
      if (event.run_id) setActiveRunId(event.run_id);
    } else if (event.type === 'token' && continuationMsgIdRef.current) {
      const continuationMsgId = continuationMsgIdRef.current;
      const tok = event.token || '';
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) => {
              if (m.id !== continuationMsgId) return m;
              const tl = [...(m.timeline || [])];
              const last = tl[tl.length - 1];
              if (last && last.type === 'text') {
                tl[tl.length - 1] = { ...last, text: `${last.text || ''}${tok}` };
              } else {
                tl.push({ type: 'text', text: tok });
              }
              return { ...m, content: `${m.content || ''}${tok}`, timeline: tl };
            }),
          }
        )
      );
    } else if (event.type === 'artifact') {
      mergeArtifact(event);
      const continuationMsgId = continuationMsgIdRef.current;
      if (continuationMsgId) {
        const entry = { type: 'artifact', op: event.op, path: event.path, additions: event.additions, deletions: event.deletions };
        setConversations((prev) =>
          prev.map((c) =>
            c.id !== convId ? c : {
              ...c,
              messages: c.messages.map((m) =>
                m.id === continuationMsgId
                  ? { ...m, timeline: [...(m.timeline || []), entry], files: mergeMessageFile(m.files, event) }
                  : m
              ),
            }
          )
        );
      }
    } else if ((event.type === 'think' || event.type === 'plan') && continuationMsgIdRef.current) {
      // Completed thought: it becomes a ReasoningStep in the bubble and ends the
      // live ticker that was showing the same text as it was written.
      const continuationMsgId = continuationMsgIdRef.current;
      const step = { kind: event.type, step: event.step, content: event.content };
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) =>
              m.id === continuationMsgId
                ? {
                    ...m,
                    reasoning: [...(m.reasoning || []), step],
                    timeline: [...(m.timeline || []), { type: 'reasoning', ...step }],
                    thinking_live: '',
                  }
                : m
            ),
          }
        )
      );
    } else if (event.type === 'think_delta' && continuationMsgIdRef.current) {
      const continuationMsgId = continuationMsgIdRef.current;
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) =>
              m.id === continuationMsgId
                ? { ...m, thinking_live: appendLiveThought(m.thinking_live, event.delta) }
                : m
            ),
          }
        )
      );
    } else if (event.type === 'done' && continuationMsgIdRef.current) {
      const continuationMsgId = continuationMsgIdRef.current;
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) =>
              m.id === continuationMsgId
                ? {
                    ...m,
                    content: (m.content || event.response || '').trim() || event.response || '',
                    thinking_live: '',
                    error: !event.ok,
                    run_id: event.run_id || m.run_id,
                    inbound_tokens: event.usage?.inbound_tokens ?? null,
                    outbound_tokens: event.usage?.outbound_tokens ?? null,
                    total_tokens: event.usage?.total_tokens ?? null,
                    tool_calls: event.tool_calls ?? null,
                    duration_ms: event.duration_ms ?? null,
                    context_used: event.usage?.context_used ?? null,
                    context_window: event.usage?.context_window ?? null,
                    context_overflow: event.error_code === 'context_overflow',
                  }
                : m
            ),
          }
        )
      );
      continuationMsgIdRef.current = null;
    } else if (event.type === 'session_done') {
      continuationMsgIdRef.current = null;
    }
  });

  // Keep ref in sync so loadProcessData can check for existing data without a dep cycle
  useEffect(() => { processInsightsRef.current = processInsights; }, [processInsights]);

  const loadProcessData = useCallback(async (runId) => {
    if (!runId) return;
    const silent = processInsightsRef.current.message_runs.length > 0;
    if (!silent) {
      setProcessLoading(true);
      setProcessError('');
    }
    try {
      const insightsRes = await getMessageInsights(runId);
      const raw = insightsRes.data || {
          messages: [],
          tools: [],
          thinking: [],
          message_runs: [],
          token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
        };
      const allowed = conversationRunIds;
      // Rebuild the Artifacts panel from persisted diffs for runs in this conversation.
      const rawArtifacts = (raw.artifacts || []).filter((a) => {
        const rid = String(a?.run_id || '');
        return rid ? allowed.has(rid) : true;
      });
      if (rawArtifacts.length) {
        setArtifacts((prev) => {
          const next = { ...prev };
          for (const a of rawArtifacts) {
            if (a?.path) next[a.path] = { ...a };
          }
          return next;
        });
      }
      const filteredRuns = (raw.message_runs || []).filter((mr) => {
        const rid = String(mr?.run_id || '');
        return rid ? allowed.has(rid) : false;
      });
      const filteredTools = (raw.tools || []).filter((t) => {
        const rid = String(t?.run_id || '');
        return rid ? allowed.has(rid) : false;
      });

      // Merge fetched run data with existing runs — the API returns data for only
      // one run at a time, so we must preserve previously-loaded runs rather than
      // replacing the whole list. Runs with matching run_id are updated in-place.
      setProcessInsights((prev) => {
        const newRunIds = new Set(filteredRuns.map((mr) => String(mr?.run_id || mr?.message_id || '')));
        const preserved = (prev.message_runs || []).filter((mr) => {
          const id = String(mr?.run_id || mr?.message_id || '');
          return id && !newRunIds.has(id);
        });
        const mergedRuns = [...preserved, ...filteredRuns];
        const newToolRunIds = new Set(filteredTools.map((t) => String(t?.run_id || '')));
        const preservedTools = (prev.tools || []).filter((t) => {
          const id = String(t?.run_id || '');
          return id && !newToolRunIds.has(id);
        });
        const inTok = mergedRuns.reduce((s, mr) => s + (Number(mr?.inbound_tokens) || 0), 0);
        const outTok = mergedRuns.reduce((s, mr) => s + (Number(mr?.outbound_tokens) || 0), 0);
        const totalTok = mergedRuns.reduce(
          (s, mr) => s + (Number(mr?.total_tokens) || ((Number(mr?.inbound_tokens) || 0) + (Number(mr?.outbound_tokens) || 0))),
          0,
        );
        return {
          ...prev,
          session_id: raw.session_id || prev.session_id,
          message_runs: mergedRuns,
          tools: [...preservedTools, ...filteredTools],
          token_usage: { inbound_tokens: inTok, outbound_tokens: outTok, total_tokens: totalTok },
          // Runs load one at a time, so the session's peak is the running max.
          context_peak: Math.max(prev.context_peak || 0, raw.context_window?.input_tokens_used || 0),
          context_window_tokens: raw.context_window?.context_window_tokens || prev.context_window_tokens || 0,
        };
      });
    } catch (err) {
      if (!silent) setProcessError(err.response?.data?.detail || t('chat.failedToLoadProcess'));
    } finally {
      if (!silent) setProcessLoading(false);
    }
  }, [conversationRunIds, t]);

  // Load all unloaded conversation runs whenever the panel is open and conversationRunIds changes.
  // Build view also needs this data (for the Artifacts panel), even with the
  // Process panel closed. Uses a ref to avoid re-fetching runs already loaded.
  useEffect(() => {
    if ((!processOpen && viewMode !== 'build') || loading) return;
    const toLoad = Array.from(conversationRunIds).filter(rid => !loadedRunIdsRef.current.has(rid));
    if (!toLoad.length) return;
    toLoad.forEach(runId => {
      loadedRunIdsRef.current.add(runId);
      loadProcessData(runId);
    });
  }, [processOpen, viewMode, activeRunId, loading, loadProcessData, conversationRunIds]);

  useEffect(() => {
    if (selectedWorkspace) return;
    setPendingAttachments((prev) => prev.map((a) => ({ ...a, store_to_workspace: false })));
  }, [selectedWorkspace]);

  // ---- textarea auto-resize ----
  const resizeTextarea = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
  }, []);

  const onPickFiles = useCallback(async (e) => {
    const picked = Array.from(e.target.files || []);
    if (!picked.length) return;

    setAttachmentError('');
    const availableSlots = Math.max(0, MAX_ATTACHMENT_COUNT - pendingAttachments.length);
    const toRead = picked.slice(0, availableSlots);
    const nextItems = [];

    for (const file of toRead) {
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setAttachmentError(`"${file.name}" exceeds 5 MB.`);
        continue;
      }
      try {
        const content = await file.text();
        nextItems.push({
          id: genId(),
          filename: file.name || `attachment_${Date.now()}.txt`,
          content,
          size: file.size,
          store_to_workspace: false,
        });
      } catch {
        setAttachmentError(`Failed to read "${file.name}".`);
      }
    }

    if (picked.length > availableSlots) {
      setAttachmentError(`Only ${MAX_ATTACHMENT_COUNT} attachments are allowed per message.`);
    }

    if (nextItems.length) {
      setPendingAttachments((prev) => [...prev, ...nextItems]);
    }
    e.target.value = '';
  }, [pendingAttachments.length]);

  const removeAttachment = useCallback((id) => {
    setPendingAttachments((prev) => prev.filter((a) => a.id !== id));
  }, []);

  const toggleAttachmentStore = useCallback((id, checked) => {
    setPendingAttachments((prev) =>
      prev.map((a) => (a.id === id ? { ...a, store_to_workspace: checked } : a)),
    );
  }, []);

  // ---- context references (hub entities attached to the next message) ----
  // The kind catalog is fetched once, lazily: it only matters when the user
  // actually opens the attach menu.
  useEffect(() => {
    if (!attachMenuOpen || contextKinds.length) return;
    getContextKinds()
      .then((r) => setContextKinds(r.data || []))
      .catch(() => setContextKinds([]));
  }, [attachMenuOpen, contextKinds.length]);

  const addReferences = useCallback((items) => {
    setAttachmentError('');
    setPendingReferences((prev) => {
      const seen = new Set(prev.map((r) => `${r.kind}:${r.id}`));
      const next = [...prev];
      for (const item of items || []) {
        const key = `${item.kind}:${item.id}`;
        if (seen.has(key)) continue;
        if (next.length >= MAX_REFERENCE_COUNT) {
          setAttachmentError(`Only ${MAX_REFERENCE_COUNT} attached entities are allowed per message.`);
          break;
        }
        seen.add(key);
        next.push(item);
      }
      return next;
    });
  }, []);

  const removeReference = useCallback((kind, id) => {
    setPendingReferences((prev) => prev.filter((r) => !(r.kind === kind && r.id === id)));
  }, []);

  // Reset process panel when switching conversations. Clearing sessionId
  // releases the session channel on the shared stream automatically.
  useEffect(() => {
    continuationMsgIdRef.current = null;
    setSessionId(null);
    loadedRunIdsRef.current = new Set();
    setArtifacts({});
    setProcessInsights({
      messages: [],
      tools: [],
      thinking: [],
      message_runs: [],
      token_usage: { inbound_tokens: 0, outbound_tokens: 0, total_tokens: 0 },
      context_peak: 0,
      context_window_tokens: 0,
    });
  }, [currentConvId]);

  // Merge a streamed/persisted artifact into the per-path map (last write wins).
  const mergeArtifact = useCallback((art) => {
    if (!art || !art.path) return;
    setArtifacts((prev) => ({ ...prev, [art.path]: { ...art } }));
  }, []);

  // ---- new conversation ----
  // Don't create a conversation record yet — just land on the empty start page.
  // sendMessage lazily creates the conversation when the first message is sent,
  // so repeated "New chat" clicks never pile up empty conversations in the list.
  const newConversation = useCallback(() => {
    setCurrentConvId(null);
    setInput('');
    navigate('/chat');
    setTimeout(() => textareaRef.current?.focus(), 0);
  }, [navigate]);

  // ---- delete conversation ----
  const deleteConversation = useCallback((id, e) => {
    e.stopPropagation();
    const conv = conversations.find((c) => c.id === id);
    const label = conv?.title || t('chat.thisConversation');
    if (!window.confirm(t('chat.confirmDeleteConversation', { label }))) return;
    removeConversation(id);
    if (currentConvId === id) navigate('/chat');
  }, [conversations, currentConvId, navigate, removeConversation, t]);

  // ---- slash command selection ----
  const selectCommand = useCallback(async (cmd) => {
    setCommandMenuOpen(false);
    setCommandMenuIndex(0);
    if (cmd.name === '/clear') {
      if (currentConvId) {
        setConversations((prev) => prev.map((c) => c.id === currentConvId ? { ...c, messages: [] } : c));
      }
      setInput('');
      return;
    }
    if (cmd.name === '/new') {
      setCurrentConvId(null);
      setInput('');
      setTimeout(() => textareaRef.current?.focus(), 0);
      return;
    }
    if (cmd.name === '/help') {
      const lines = allCommands
        .map((c) => `**${c.name}** — ${c.descriptionKey ? t(c.descriptionKey) : c.description}`)
        .join('\n');
      const helpMsg = { id: genId(), role: 'agent', content: `${t('chat.availableCommands')}\n\n${lines}`, error: false };
      if (currentConvId) {
        setConversations((prev) => prev.map((c) => c.id === currentConvId ? { ...c, messages: [...c.messages, helpMsg] } : c));
      }
      setInput('');
      return;
    }
    if (cmd.name === '/config') {
      const agentObj = agents.find((a) => a.id === selectedAgent) || {};
      const inherit = t('chat.inheritGlobal');
      const provider = agentObj.provider || inherit;
      const model = agentObj.model || inherit;
      const baseUrl = agentObj.base_url || '—';
      const temperature = agentObj.temperature != null ? agentObj.temperature : inherit;
      const maxTokens = agentObj.max_tokens != null ? agentObj.max_tokens : inherit;
      const tools = (agentObj.tools || []).length > 0 ? (agentObj.tools || []).join(', ') : '—';
      const streaming = agentObj.streaming ? t('common.yes') : t('common.no');
      const verbose = agentObj.verbose ? t('common.yes') : t('common.no');
      setInput('');
      let systemPrompt = agentObj.system_prompt || '';
      try {
        const defResp = await getAgentDefinition(selectedAgent);
        systemPrompt = defResp.data?.system_prompt || systemPrompt;
      } catch { /* keep the default system prompt */ }
      const lines = [
        `**${t('chat.config.agent')}:** ${agentObj.name || selectedAgent} (\`${agentObj.id || selectedAgent}\`)`,
        `**${t('chat.config.description')}:** ${agentObj.description || '—'}`,
        `**${t('chat.config.domain')}:** ${agentObj.domain || '—'}`,
        ``,
        `**${t('chat.config.provider')}:** ${provider}`,
        `**${t('chat.config.model')}:** ${model}`,
        `**${t('chat.config.baseUrl')}:** ${baseUrl}`,
        `**${t('chat.config.temperature')}:** ${temperature}`,
        `**${t('chat.config.maxTokens')}:** ${maxTokens}`,
        `**${t('chat.config.streaming')}:** ${streaming}`,
        `**${t('chat.config.verbose')}:** ${verbose}`,
        ``,
        `**${t('chat.config.tools')}:** ${tools}`,
        ``,
        `**${t('chat.config.systemPrompt')}:**\n${systemPrompt || '—'}`,
      ].join('\n');
      const configMsg = { id: genId(), role: 'agent', content: lines, error: false };
      if (currentConvId) {
        setConversations((prev) => prev.map((c) => c.id === currentConvId ? { ...c, messages: [...c.messages, configMsg] } : c));
      }
      return;
    }
    setInput(cmd.template);
    setTimeout(() => textareaRef.current?.focus(), 0);
  }, [agents, allCommands, currentConvId, selectedAgent, setConversations, t]);

  // ---- send message ----
  const sendMessage = useCallback(async (overrideText) => {
    // Button clicks call this with their value; the onClick handler passes a
    // SyntheticEvent, so only honour an explicit string override.
    const text = (typeof overrideText === 'string' ? overrideText : input).trim();
    const hasAttachments = pendingAttachments.length > 0;
    const hasReferences = pendingReferences.length > 0;
    const isFlowMode = targetMode === 'flow';
    const isTeamMode = targetMode === 'team';
    // Flows and teams both answer with several bubbles rather than one streamed
    // reply, so the single-assistant-bubble path is skipped for both.
    const isMultiAgent = isFlowMode || isTeamMode;
    if ((!text && !hasAttachments && !hasReferences) || loading) return;
    if (!targetId(targetMode, { selectedAgent, selectedFlow, selectedTeam })) return;

    // Handle special client-side slash commands
    if (text === '/clear') { selectCommand({ name: '/clear' }); return; }
    if (text === '/new') { selectCommand({ name: '/new' }); return; }
    if (text === '/help') { selectCommand({ name: '/help' }); return; }
    if (text === '/config') { selectCommand({ name: '/config' }); return; }

    const referenceLine = hasReferences
      ? t('chat.attachedEntities', { entities: pendingReferences.map((r) => r.label || r.id).join(', ') })
      : '';
    const attachmentLine = [
      hasReferences ? referenceLine : '',
      hasAttachments
        ? t('chat.attachedFiles', { files: pendingAttachments.map((a) => a.filename).join(', ') })
        : '',
    ].filter(Boolean).join('\n');
    const userMsgText = [text, attachmentLine].filter(Boolean).join('\n');
    const userMsg = { id: genId(), role: 'user', content: userMsgText };

    // Ensure there is an active conversation
    let convId = currentConvId;
    if (!convId) {
      convId = genId();
      const basis = text || attachmentLine || t('chat.newChat');
      const title = basis.length > 50 ? basis.slice(0, 50) + '…' : basis;
      const newConv = {
        id: convId,
        title,
        agent_id: targetMode === 'agent' ? selectedAgent : null,
        flow_id: isFlowMode ? selectedFlow : null,
        team_id: isTeamMode ? selectedTeam : null,
        target_mode: targetMode,
        workspace: selectedWorkspace,
        project_id: selectedProject || null,
        messages: [],
        created_at: new Date().toISOString(),
      };
      setConversations((prev) => [newConv, ...prev]);
      setCurrentConvId(convId);
      navigate(`/chat/${convId}`);
    }

    // Append user message
    setConversations((prev) =>
      prev.map((c) =>
        c.id === convId
          ? {
              ...c,
              messages: [...c.messages, userMsg],
              title: c.messages.length === 0
                ? ((text || attachmentLine).length > 50 ? (text || attachmentLine).slice(0, 50) + '…' : (text || attachmentLine))
                : c.title,
            }
          : c,
      ),
    );

    setInput('');
    setPendingAttachments([]);
    setPendingReferences([]);
    setAttachmentError('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setLoading(true);

    const ctrl = new AbortController();
    abortCtrlRef.current = ctrl;

    // Use the conversation's own workspace (set at creation time), not the current global selection.
    // This locks the conversation to the workspace it was started in.
    const convRecord = conversations.find((c) => c.id === convId);
    const effectiveWorkspace = convRecord?.workspace || selectedWorkspace;
    const historyPayload = ((convRecord?.messages || [])
      .filter((m) => (m.role === 'user' || m.role === 'agent') && String(m.content || '').trim())
      .slice(-40)
      .map((m) => ({
        role: m.role,
        content: String(m.content || ''),
      })));
    const autoTitleBasis = (text || attachmentLine || '').trim();
    const autoTitle = autoTitleBasis
      ? (autoTitleBasis.length > 50 ? autoTitleBasis.slice(0, 50) + '…' : autoTitleBasis)
      : t('chat.newConversation');
    // The placeholder check compares against every locale's wording so a title
    // written in one language is still recognised after switching.
    const placeholderTitles = new Set(
      LANGUAGES.map((l) => translate(l.code, 'chat.newConversation').trim().toLowerCase())
    );
    const isPlaceholderTitle = !convRecord?.title || placeholderTitles.has(convRecord.title.trim().toLowerCase());
    const convTitle = (!convRecord || (convRecord.messages || []).length === 0 || isPlaceholderTitle)
      ? autoTitle
      : convRecord.title;

    try {
      // In agent mode we create one assistant bubble up front and stream into it.
      // In flow mode we wait for node_start events and create one bubble per node.
      const assistantId = isMultiAgent ? null : genId();
      // Maps node_id -> message bubble id for flow mode.
      const nodeMsgIds = {};
      let currentNodeId = null;
      if (!isMultiAgent) {
        setConversations((prev) =>
          prev.map((c) =>
            c.id === convId
              ? {
                  ...c,
                  messages: [
                    ...c.messages,
                    {
                      id: assistantId,
                      role: 'agent',
                      agent_id: selectedAgent,
                      content: '',
                      error: false,
                      run_id: null,
                      inbound_tokens: null,
                      outbound_tokens: null,
                      total_tokens: null,
                      tool_calls: null,
                      duration_ms: null,
                    },
                  ],
                }
              : c,
          ),
        );
      }

      // Mutated from inside the onEvent closure; read after the stream resolves.
      let finalPayload = null;
      let runId = null;

      await streamChat({
        signal: ctrl.signal,
        body: {
          agent_id: targetMode === 'agent' ? selectedAgent : null,
          flow_id: isFlowMode ? selectedFlow : null,
          team_id: isTeamMode ? selectedTeam : null,
          message: text,
          workspace: effectiveWorkspace || null,
          project_id: convRecord?.project_id || null,
          conversation_id: convId,
          conversation_title: convTitle,
          client_id: clientId,
          history: historyPayload,
          attachments: pendingAttachments.map((a) => ({
            filename: a.filename,
            content: a.content,
            store_to_workspace: Boolean(a.store_to_workspace && selectedWorkspace),
          })),
          // Pointers only — the server renders each entity into the prompt at
          // request time (chat/references.py), so nothing stale is sent.
          references: pendingReferences.map((r) => ({ kind: r.kind, id: r.id, label: r.label || '' })),
        },
        onEvent: (event) => {
          // Helper: returns the id of the assistant bubble that should receive
          // streaming events for the current event. In flow mode this is the
          // bubble for the active node; in agent mode it's the single assistantId.
          const targetMsgId = () => {
            if (isFlowMode) {
              const nid = event.node_id || currentNodeId;
              return nid ? nodeMsgIds[nid] : null;
            }
            return assistantId;
          };

          // A team answers as a conversation: every board entry becomes its own
          // bubble, labelled with who said it and who it was for. There are no
          // token events to stream — the members' turns are whole replies.
          if (event.type === 'team_message') {
            if (event.run_id) { runId = event.run_id; setActiveRunId(event.run_id); }
            const recipients = (event.recipients || []).filter((r) => r !== '*');
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: [
                    ...c.messages,
                    {
                      id: genId(),
                      role: 'agent',
                      agent_id: event.sender,
                      agent_label: recipients.length
                        ? `${event.sender} → ${recipients.join(', ')}`
                        : event.sender,
                      content: event.content || '',
                      error: event.kind === 'error',
                      run_id: event.run_id || null,
                      inbound_tokens: null,
                      outbound_tokens: null,
                      total_tokens: event.tokens || null,
                      tool_calls: null,
                      duration_ms: null,
                    },
                  ],
                },
              ),
            );
            return;
          }
          if (event.type === 'team_meta') {
            if (event.session_id) setSessionId(event.session_id);
            return;
          }

          // Delegated child runs (run_agent_tool) stream their inner events
          // tagged with `delegation` + the child run_id. Fold them into a nested
          // `delegation` timeline entry instead of the top-level timeline, so the
          // UI renders a live nested block. Handled first, then return, so the
          // tagged tool_start/tool_end/think below don't also hit the top level.
          if (event.type === 'delegation_start' || event.type === 'delegation_end' || event.delegation) {
            const tgt = targetMsgId();
            if (tgt) {
              setConversations((prev) =>
                prev.map((c) =>
                  c.id !== convId ? c : {
                    ...c,
                    messages: c.messages.map((m) => {
                      if (m.id !== tgt) return m;
                      const messageRunId = m.run_id || null;
                      let tl = m.timeline || [];
                      if (event.type === 'delegation_start') {
                        tl = appendUnderDelegation(tl, event.parent_run_id, messageRunId, {
                          type: 'delegation',
                          run_id: event.run_id,
                          agent_id: event.agent_id,
                          agent_name: event.agent_name || event.agent_id,
                          depth: event.depth || 1,
                          input: event.input || '',
                          running: true,
                          ok: null,
                          timeline: [],
                        });
                      } else if (event.type === 'delegation_end') {
                        tl = mapDelegation(tl, event.run_id, (d) => ({
                          ...d,
                          running: false,
                          ok: event.ok,
                          error: event.error || '',
                          duration_ms: event.duration_ms,
                        }));
                      } else if (event.type === 'think' || event.type === 'plan') {
                        tl = appendIntoDelegation(tl, event.run_id, {
                          type: 'reasoning', kind: event.type, step: event.step, content: event.content,
                        });
                      } else if (event.type === 'tool_start') {
                        tl = appendIntoDelegation(tl, event.run_id, {
                          type: 'tool', step: event.step, tool: event.tool, input: event.input, output: null, running: true,
                        });
                      } else if (event.type === 'tool_end') {
                        tl = resolveDelegationTool(tl, event.run_id, { output: event.output });
                      } else if (event.type === 'tool_error') {
                        tl = resolveDelegationTool(tl, event.run_id, { output: `ERROR: ${event.error}`, error: true });
                      }
                      return { ...m, timeline: tl };
                    }),
                  },
                ),
              );
            }
            return;
          }

          if (event.type === 'flow_meta') {
            // Flow chat: capture session_id and flow agent_id mapping for bubbles.
            if (event.session_id) setSessionId(event.session_id);
          } else if (event.type === 'node_start') {
            // Create a new assistant bubble for this node.
            currentNodeId = event.node_id;
            const msgId = genId();
            nodeMsgIds[event.node_id] = msgId;
            const nodeRunId = event.run_id || null;
            if (nodeRunId) {
              runId = nodeRunId;
              setActiveRunId(nodeRunId);
            }
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: [
                    ...c.messages,
                    {
                      id: msgId,
                      role: 'agent',
                      agent_id: event.agent_id || event.agent_label || '',
                      agent_label: event.agent_label || '',
                      node_id: event.node_id,
                      content: '',
                      error: false,
                      run_id: nodeRunId,
                      inbound_tokens: null,
                      outbound_tokens: null,
                      total_tokens: null,
                      tool_calls: null,
                      duration_ms: null,
                    },
                  ],
                },
              ),
            );
            if (processOpen && nodeRunId) {
              setProcessInsights((prev) => ({
                ...prev,
                message_runs: [
                  ...(prev.message_runs || []),
                  {
                    message_id: nodeRunId,
                    run_id: nodeRunId,
                    timestamp: new Date().toISOString(),
                    input: userMsgText,
                    output: '',
                    tools: [],
                    thinking: [],
                    inbound_tokens: 0,
                    outbound_tokens: 0,
                    total_tokens: 0,
                    tool_calls: 0,
                    duration_ms: 0,
                    agent_id: event.agent_id || '',
                  },
                ],
              }));
            }
          } else if (event.type === 'node_done') {
            // Finalize one node's bubble; the surrounding loop continues into the next node.
            const msgId = nodeMsgIds[event.node_id];
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === msgId
                      ? {
                          ...m,
                          // Prefer the node's final response over accumulated stream
                          // tokens (see the agent-mode `done` handler) to avoid repeating
                          // the answer when the model re-states it across LLM turns.
                          content: (event.response || '').trim() || (event.response_obj ? '' : stripUiBlock(m.content)) || '',
                          entities: event.entities || m.entities || null,
                          thinking_live: '',
                          error: !event.ok,
                          run_id: event.run_id || m.run_id,
                          inbound_tokens: event.usage?.inbound_tokens ?? null,
                          outbound_tokens: event.usage?.outbound_tokens ?? null,
                          total_tokens: event.usage?.total_tokens ?? null,
                          tool_calls: event.tool_calls ?? null,
                          duration_ms: event.duration_ms ?? null,
                        }
                      : m
                  ),
                },
              ),
            );
            if (processOpen) {
              setProcessInsights((prev) => {
                const inTok = event.usage?.inbound_tokens || 0;
                const outTok = event.usage?.outbound_tokens || 0;
                const totTok = event.usage?.total_tokens || (inTok + outTok);
                return {
                  ...prev,
                  token_usage: {
                    inbound_tokens: (prev.token_usage?.inbound_tokens || 0) + inTok,
                    outbound_tokens: (prev.token_usage?.outbound_tokens || 0) + outTok,
                    total_tokens: (prev.token_usage?.total_tokens || 0) + totTok,
                  },
                  context_peak: Math.max(prev.context_peak || 0, event.usage?.context_used || 0),
                  context_window_tokens: event.usage?.context_window || prev.context_window_tokens || 0,
                  message_runs: (prev.message_runs || []).map((mr) =>
                    mr.run_id === event.run_id
                      ? {
                          ...mr,
                          output: event.response || mr.output || '',
                          inbound_tokens: inTok,
                          outbound_tokens: outTok,
                          total_tokens: totTok,
                          tool_calls: event.tool_calls ?? mr.tool_calls ?? 0,
                          duration_ms: event.duration_ms ?? mr.duration_ms ?? 0,
                        }
                      : mr
                  ),
                };
              });
            }
          } else if (event.type === 'meta' && event.run_id) {
            runId = event.run_id;
            setActiveRunId(runId);
            if (event.session_id) setSessionId(event.session_id);
            if (processOpen && !isMultiAgent) {
              // In flow mode, node_start already created the process row.
              setProcessInsights((prev) => ({
                ...prev,
                message_runs: [
                  ...(prev.message_runs || []),
                  {
                    message_id: runId,
                    run_id: runId,
                    timestamp: new Date().toISOString(),
                    input: userMsgText,
                    output: '',
                    tools: [],
                    thinking: [],
                    inbound_tokens: 0,
                    outbound_tokens: 0,
                    total_tokens: 0,
                    tool_calls: 0,
                    duration_ms: 0,
                  },
                ],
              }));
            }
            if (!isMultiAgent) {
              setConversations((prev) =>
                prev.map((c) =>
                  c.id !== convId ? c : {
                    ...c,
                    messages: c.messages.map((m) => (m.id === assistantId ? { ...m, run_id: runId } : m)),
                  },
                ),
              );
            }
          } else if (event.type === 'think_delta') {
            // A slice of reasoning the model is still writing. Shown as a
            // three-line ticker under the "working" indicator until the matching
            // `think` event arrives with the completed thought.
            const tgt = targetMsgId();
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === tgt
                      ? { ...m, thinking_live: appendLiveThought(m.thinking_live, event.delta) }
                      : m
                  ),
                },
              ),
            );
          } else if (event.type === 'think' || event.type === 'plan') {
            // Reasoning steps are appended in execution order so the UI can
            // render each one inline, before the response that followed it.
            // Also appended to `timeline` (the Build-view chronological feed).
            const tgt = targetMsgId();
            const step = { kind: event.type, step: event.step, content: event.content };
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === tgt
                      ? {
                          ...m,
                          reasoning: [...(m.reasoning || []), step],
                          timeline: [...(m.timeline || []), { type: 'reasoning', ...step }],
                          // The thought is complete and now renders as a collapsible
                          // ReasoningStep, so the live ticker of the same text goes away.
                          thinking_live: '',
                          // The think/plan content arrives complete in this event
                          // (it's the tool-call input), so the ChatTrail box is
                          // the marker. Don't also set running_tool — that renders a
                          // stale "running" spinner *below* an already-finished thought.
                        }
                      : m
                  ),
                },
              ),
            );
            // Native model thoughts also feed the Process column live.
            if (processOpen && event.native) {
              setProcessInsights((prev) => ({
                ...prev,
                message_runs: (prev.message_runs || []).map((mr, idx) =>
                  idx === (prev.message_runs || []).length - 1
                    ? { ...mr, reasoning: [...(mr.reasoning || []), { step: event.step, content: event.content, native: true }] }
                    : mr
                ),
              }));
            }
          } else if (event.type === 'artifact') {
            // File change — surface in the Artifacts panel and inline in the feed.
            mergeArtifact(event);
            const tgt = targetMsgId();
            const entry = {
              type: 'artifact',
              op: event.op,
              path: event.path,
              additions: event.additions,
              deletions: event.deletions,
            };
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === tgt
                      ? {
                          ...m,
                          timeline: [...(m.timeline || []), entry],
                          // Persisted alongside the reply so the chat bubble lists
                          // what the agent changed, not just the Artifacts column.
                          files: mergeMessageFile(m.files, event),
                        }
                      : m
                  ),
                },
              ),
            );
          } else if (event.type === 'tool_start') {
            // Update message bubble to show the running tool name, and append a
            // tool entry to the Build-view timeline (resolved on tool_end).
            const tgt = targetMsgId();
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) =>
                    m.id === tgt
                      ? {
                          ...m,
                          running_tool: event.tool,
                          timeline: [
                            ...(m.timeline || []),
                            { type: 'tool', step: event.step, tool: event.tool, input: event.input, output: null, running: true },
                          ],
                        }
                      : m
                  ),
                },
              ),
            );
            if (processOpen) {
              setProcessInsights((prev) => ({
                ...prev,
                tools: [
                  ...(prev.tools || []),
                  {
                    step: event.step,
                    tool: event.tool,
                    input: event.input,
                    output: null,
                    running: true,
                  },
                ],
                message_runs: (prev.message_runs || []).map((mr, idx) =>
                  idx === (prev.message_runs || []).length - 1
                    ? {
                        ...mr,
                        tools: [
                          ...(mr.tools || []),
                          {
                            step: event.step,
                            tool: event.tool,
                            input: event.input,
                            output: null,
                            running: true,
                          },
                        ],
                        tool_calls: ((mr.tool_calls || 0) + 1),
                      }
                    : mr
                ),
              }));
            }
          } else if (event.type === 'tool_end') {
            // Keep running_tool set so the name stays visible until the next token arrives.
            // Resolve the last running tool entry in the Build-view timeline.
            {
              const tgt = targetMsgId();
              setConversations((prev) =>
                prev.map((c) =>
                  c.id !== convId ? c : {
                    ...c,
                    messages: c.messages.map((m) => {
                      if (m.id !== tgt || !m.timeline) return m;
                      const tl = [...m.timeline];
                      for (let i = tl.length - 1; i >= 0; i -= 1) {
                        if (tl[i].type === 'tool' && tl[i].running) {
                          tl[i] = { ...tl[i], output: event.output, running: false };
                          break;
                        }
                      }
                      return { ...m, timeline: tl };
                    }),
                  },
                ),
              );
            }
            if (processOpen) {
              setProcessInsights((prev) => {
                const tools = [...(prev.tools || [])];
                for (let i = tools.length - 1; i >= 0; i -= 1) {
                  if (tools[i].running) {
                    tools[i] = { ...tools[i], output: event.output, running: false };
                    break;
                  }
                }
                const message_runs = (prev.message_runs || []).map((mr, idx) => {
                  if (idx !== (prev.message_runs || []).length - 1) return mr;
                  const mrTools = [...(mr.tools || [])];
                  for (let i = mrTools.length - 1; i >= 0; i -= 1) {
                    if (mrTools[i].running) {
                      mrTools[i] = { ...mrTools[i], output: event.output, running: false };
                      break;
                    }
                  }
                  return { ...mr, tools: mrTools };
                });
                return { ...prev, tools, message_runs };
              });
            }
          } else if (event.type === 'token') {
            const tgt = targetMsgId();
            const tok = event.token || '';
            setConversations((prev) =>
              prev.map((c) =>
                c.id !== convId ? c : {
                  ...c,
                  messages: c.messages.map((m) => {
                    if (m.id !== tgt) return m;
                    // Coalesce contiguous tokens into the trailing text segment so
                    // the Build-view feed shows continuous prose, not per-token noise.
                    const tl = [...(m.timeline || [])];
                    const last = tl[tl.length - 1];
                    if (last && last.type === 'text') {
                      tl[tl.length - 1] = { ...last, text: `${last.text || ''}${tok}` };
                    } else {
                      tl.push({ type: 'text', text: tok });
                    }
                    return {
                      ...m,
                      content: `${m.content || ''}${tok}`,
                      running_tool: null,
                      timeline: tl,
                    };
                  }),
                },
              ),
            );
            if (processOpen) {
              setProcessInsights((prev) => ({
                ...prev,
                message_runs: (prev.message_runs || []).map((mr, idx) =>
                  idx === (prev.message_runs || []).length - 1
                    ? { ...mr, output: `${mr.output || ''}${event.token || ''}` }
                    : mr
                ),
              }));
            }
          } else if (event.type === 'done') {
            finalPayload = event;
            const resolvedRunId = runId || event.run_id || null;
            if (resolvedRunId) setActiveRunId(resolvedRunId);
            // In flow mode the per-node bubbles were already finalized via
            // node_done, and in team mode every reply is already on the board,
            // so the overall "done" event only carries run-level metadata.
            if (!isMultiAgent) {
              setConversations((prev) =>
                prev.map((c) =>
                  c.id !== convId ? c : {
                    ...c,
                    messages: c.messages.map((m) =>
                      m.id === assistantId
                        ? {
                            ...m,
                            // Prefer the backend's final response (the de-duplicated
                            // AgentFinish output). Streamed tokens accumulate text from
                            // every LLM turn — including forced-review re-statements — so
                            // using them as-is can repeat the answer. Fall back to the
                            // streamed content only when no final response was sent.
                            content: (event.response || '').trim() || (event.response_obj ? '' : stripUiBlock(m.content)) || '',
                            // Structured response (buttons / Telegram keyboard / …); rendered
                            // as interactive UI under the bubble. null for plain replies.
                            response_obj: event.response_obj || null,
                            // Service entities this turn touched, rendered as links
                            // under the reply (see EntityLinks).
                            entities: event.entities || m.entities || null,
                            thinking_live: '',
                            error: !event.ok,
                            run_id: resolvedRunId,
                            inbound_tokens: event.usage?.inbound_tokens ?? m.inbound_tokens ?? null,
                            outbound_tokens: event.usage?.outbound_tokens ?? m.outbound_tokens ?? null,
                            total_tokens: event.usage?.total_tokens ?? m.total_tokens ?? null,
                            tool_calls: event.tool_calls ?? m.tool_calls ?? null,
                            duration_ms: event.duration_ms ?? m.duration_ms ?? null,
                            // How full the model's window was at this turn's
                            // biggest call, kept on the message so the meter
                            // survives switching conversations and reloading —
                            // the transcript is the context, and it is what
                            // localStorage holds.
                            context_used: event.usage?.context_used ?? m.context_used ?? null,
                            context_window: event.usage?.context_window ?? m.context_window ?? null,
                            context_overflow: event.error_code === 'context_overflow',
                          }
                        : m
                    ),
                  },
                ),
              );
            }
            if (processOpen && !isMultiAgent) {
              setProcessInsights((prev) => {
                const inTok = event.usage?.inbound_tokens || 0;
                const outTok = event.usage?.outbound_tokens || 0;
                const totTok = event.usage?.total_tokens || (inTok + outTok);
                return {
                  ...prev,
                  token_usage: {
                    inbound_tokens: (prev.token_usage?.inbound_tokens || 0) + inTok,
                    outbound_tokens: (prev.token_usage?.outbound_tokens || 0) + outTok,
                    total_tokens: (prev.token_usage?.total_tokens || 0) + totTok,
                  },
                  context_peak: Math.max(prev.context_peak || 0, event.usage?.context_used || 0),
                  context_window_tokens: event.usage?.context_window || prev.context_window_tokens || 0,
                  message_runs: (prev.message_runs || []).map((mr, idx) =>
                    idx === (prev.message_runs || []).length - 1
                      ? {
                          ...mr,
                          output: event.response || mr.output || '',
                          inbound_tokens: inTok,
                          outbound_tokens: outTok,
                          total_tokens: totTok,
                          tool_calls: event.tool_calls ?? mr.tool_calls ?? 0,
                          duration_ms: event.duration_ms ?? mr.duration_ms ?? 0,
                        }
                      : mr
                  ),
                };
              });
            }
          }
        },
      });

      if (!finalPayload) {
        if (!isMultiAgent) {
          setConversations((prev) =>
            prev.map((c) =>
              c.id !== convId ? c : {
                ...c,
                messages: c.messages.map((m) =>
                  m.id === assistantId && !m.content
                    ? { ...m, content: t('chat.noStreamedOutput'), error: true }
                    : m
                ),
              },
            ),
          );
        }
      } else if ((runId || finalPayload.run_id) && processOpen) {
        loadProcessData(runId || finalPayload.run_id);
      }
    } catch (err) {
      if (err?.name === 'AbortError') return;

      const errMsg = {
        id: genId(),
        role: 'agent',
        content: err.response?.data?.detail || err.message || t('chat.failedToGetResponse'),
        error: true,
        run_id: null,
      };
      setConversations((prev) =>
        prev.map((c) =>
          c.id === convId ? { ...c, messages: [...c.messages, errMsg] } : c,
        ),
      );
    } finally {
      setLoading(false);
      abortCtrlRef.current = null;
      // The turn is over however it ended (done, abort, network error): drop any
      // half-written thought so no bubble is left with a stale live ticker.
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== convId ? c : {
            ...c,
            messages: c.messages.map((m) => (m.thinking_live ? { ...m, thinking_live: '' } : m)),
          },
        ),
      );
    }
  }, [input, pendingAttachments, pendingReferences, targetMode, loading, selectedAgent, selectedFlow, selectedTeam, t, currentConvId, conversations, setConversations, clientId, selectedWorkspace, selectCommand, selectedProject, navigate, processOpen, mergeArtifact, loadProcessData]);

  // Build view: clicking a file chip in the transcript scrolls the always-open
  // Artifacts panel to that file's diff.
  const jumpToArtifact = useCallback((path) => {
    setTimeout(() => {
      const el = document.querySelector(`[data-artifact-path="${CSS.escape(path)}"]`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 50);
  }, []);

  const stopGeneration = () => {
    abortCtrlRef.current?.abort();
    if (activeRunId) stopMessage(activeRunId).catch(() => {});
    setLoading(false);
  };

  // True only when the conversation's binding workspace matches the active workspace.
  // Reply-as-bot is forbidden across workspaces to keep the Telegram surface
  // anchored to whichever workspace the operator is actually working in.
  const telegramReplyAllowed = Boolean(
    currentTelegramBinding
    && selectedWorkspace
    && (currentTelegramBinding.workspace || null) === selectedWorkspace
  );

  // Send the composer text back to a Telegram chat as the bot (debug surface).
  const sendAsBot = useCallback(async () => {
    if (!currentTelegramBinding) return;
    if (!telegramReplyAllowed) return;
    const text = input.trim();
    if (!text) return;
    setTelegramSending(true);
    setTelegramError('');
    try {
      await sendTelegramMessage(currentTelegramBinding.chat_id, text);
      const convId = currentTelegramBinding.conversation_id;
      setConversations((prev) =>
        prev.map((c) => c.id !== convId ? c : {
          ...c,
          messages: [...(c.messages || []), {
            id: genId(),
            role: 'user',
            content: text,
            origin: 'telegram-bot',
            createdAt: new Date().toISOString(),
          }],
        })
      );
      setInput('');
    } catch (e) {
      setTelegramError(e.response?.data?.detail || e.message || t('chat.sendFailed'));
    } finally {
      setTelegramSending(false);
    }
  }, [currentTelegramBinding, telegramReplyAllowed, input, setConversations, t]);

  const handleKeyDown = (e) => {
    if (commandMenuOpen && commandSuggestions.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setCommandMenuIndex((i) => Math.min(i + 1, commandSuggestions.length - 1));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setCommandMenuIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && commandSuggestions.length > 0)) {
        e.preventDefault();
        selectCommand(commandSuggestions[commandMenuIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setCommandMenuOpen(false);
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (currentTelegramBinding) {
        sendAsBot();
      } else {
        sendMessage();
      }
    }
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <div className="h-full flex overflow-hidden">

      {/* ── Sidebar ── */}
      <div className="w-60 flex-shrink-0 border-r border-gray-200 flex flex-col">
        <div className="p-3">
          <button
            onClick={newConversation}
            className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
          >
            <PlusCircle className="w-4 h-4" />
            {t('chat.newChat')}
          </button>
        </div>

        {/* Top panel — normal chats */}
        <div className="flex-1 min-h-0 flex flex-col">
          <div className="px-3 pt-2 pb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-gray-400 font-semibold flex-shrink-0">
            <MessageSquare className="w-3 h-3" /> {t('chat.chats')}
            {/* Chats are stored on the server; when that write keeps failing the
                conversation only exists in this tab, and saying so is the
                difference between a delay and silent loss. */}
            {syncError && (
              <span
                className="ml-auto flex items-center gap-1 normal-case tracking-normal text-amber-600"
                title={t('chat.notSavedHint')}
              >
                <AlertCircle className="w-3 h-3" /> {t('chat.notSaved')}
              </span>
            )}
          </div>
          <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
          {visibleConversations.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-10 px-3 leading-relaxed">
              No conversations yet.
              <br />
              Click <strong>{t('chat.newChat')}</strong> to start.
            </p>
          )}
          {visibleConversations.map((conv) => (
            // Rendered as a div, not a button: it contains the delete button and
            // nesting a button inside a button is invalid HTML. role/tabIndex/
            // onKeyDown restore the keyboard and a11y behaviour of a button.
            <div
              key={conv.id}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  e.currentTarget.click();
                }
              }}
              onClick={() => {
                navigate(`/chat/${conv.id}`);
                if (conv.target_mode === 'flow' && conv.flow_id) {
                  setTargetMode('flow');
                  setSelectedFlow(conv.flow_id);
                } else if (conv.target_mode === 'team' && conv.team_id) {
                  setTargetMode('team');
                  setSelectedTeam(conv.team_id);
                } else if (conv.agent_id && selectableAgents.some((a) => a.id === conv.agent_id)) {
                  setTargetMode('agent');
                  setSelectedAgent(conv.agent_id);
                }
                // Restore the conversation's project so the selector reflects the
                // scope it was created with (empty = no project).
                setSelectedProject(conv.project_id || '');
              }}
              className={`w-full text-left px-3 py-2 rounded-lg text-xs group flex items-start gap-2 transition-colors
                ${currentConvId === conv.id
                  ? 'bg-indigo-50 text-indigo-700'
                  : 'text-gray-600 hover:bg-white hover:shadow-sm'
                }`}
            >
              <MessageSquare className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 opacity-60" />
              <div className="flex-1 min-w-0">
                <div className="truncate leading-5">{conv.title}</div>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {(!selectedWorkspace || selectedWorkspace === 'default') && (
                    (!conv.workspace || conv.workspace === 'default') ? (
                      <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-gray-200 text-gray-500 font-medium">
                        {t('chat.default')}
                      </span>
                    ) : (
                      <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-600 font-medium truncate max-w-full">
                        {conv.workspace}
                      </span>
                    )
                  )}
                  {conv.target_mode === 'team' && conv.team_id ? (() => {
                    const team = teams.find((t) => t.team_id === conv.team_id);
                    return (
                      <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium truncate max-w-full">
                        <UsersRound className="w-2.5 h-2.5 flex-shrink-0" />
                        {team ? team.name : 'team'}
                      </span>
                    );
                  })() : conv.target_mode === 'flow' && conv.flow_id ? (() => {
                    const flow = flows.find((f) => f.id === conv.flow_id);
                    return (
                      <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 font-medium truncate max-w-full">
                        <Workflow className="w-2.5 h-2.5 flex-shrink-0" />
                        {flow ? flow.name : 'flow'}
                      </span>
                    );
                  })() : conv.agent_id && (() => {
                    const agent = selectableAgents.find(a => a.id === conv.agent_id);
                    return (
                      <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-600 font-medium truncate max-w-full">
                        {agent ? agent.name : conv.agent_id}
                      </span>
                    );
                  })()}
                  {conv.origin === 'telegram' && (
                    <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-sky-100 text-sky-700 font-medium">
                      <SendIcon className="w-2.5 h-2.5 flex-shrink-0" />
                      {t('chat.telegram2')}
                    </span>
                  )}
                  {conv.project_id && (() => {
                    const proj = projects.find(p => p.id === conv.project_id);
                    return proj ? (
                      <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 font-medium truncate max-w-full">
                        <FolderGit2 className="w-2.5 h-2.5 flex-shrink-0" />
                        {proj.name}
                      </span>
                    ) : null;
                  })()}
                </div>
              </div>
              <button
                onClick={(e) => deleteConversation(conv.id, e)}
                className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-gray-400 hover:text-red-500 flex-shrink-0 transition-opacity"
                title={t('chat.delete')}
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          ))}
          </div>
        </div>

        {/* Bottom panel — Telegram chats. Collapsed entirely when there are none,
            so the top panel claims the full column. When present, it takes
            exactly the bottom half of the column (flex-1 + a matching flex-1 on
            the top "Chats" panel makes them split 50/50). Overflow uses the
            macOS-style overlay scrollbar via overflow-y-auto. */}
        {visibleTelegramBindings.length > 0 && (
          <div className="flex-1 min-h-0 flex flex-col border-t border-gray-200">
            <div className="px-3 pt-2 pb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-gray-400 font-semibold flex-shrink-0">
              <SendIcon className="w-3 h-3" /> {t('chat.telegramChats')}
            </div>
            <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
              {visibleTelegramBindings.map((b) => {
                const convId = b.conversation_id;
                const isActive = currentConvId === convId;
                const agent = selectableAgents.find((a) => a.id === b.agent_id);
                return (
                  <button
                    key={`tg-${b.chat_id}`}
                    onClick={() => {
                      if (!convId) return;
                      // Ensure a local conversation entry exists so the main pane renders.
                      setConversations((prev) => {
                        if (prev.some((c) => c.id === convId)) return prev;
                        return [{
                          id: convId,
                          title: b.title || `Telegram ${b.chat_id}`,
                          agent_id: b.agent_id,
                          workspace: b.workspace || null,
                          messages: [],
                          origin: 'telegram',
                          chat_id: b.chat_id,
                          createdAt: b.created_at || new Date().toISOString(),
                        }, ...prev];
                      });
                      if (b.agent_id && selectableAgents.some((a) => a.id === b.agent_id)) {
                        setSelectedAgent(b.agent_id);
                      }
                      navigate(`/chat/${convId}`);
                    }}
                    className={`w-full text-left px-3 py-2 rounded-lg text-xs flex items-start gap-2 transition-colors
                      ${isActive ? 'bg-indigo-50 text-indigo-700' : 'text-gray-600 hover:bg-white hover:shadow-sm'}`}
                  >
                    <SendIcon className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 opacity-70" />
                    <div className="flex-1 min-w-0">
                      <div className="truncate leading-5">{b.title || t('chat.telegramChat', { id: b.chat_id })}</div>
                      <div className="mt-0.5 flex flex-wrap gap-1">
                        <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-600 font-medium truncate max-w-full">
                          {agent ? agent.name : b.agent_id}
                        </span>
                        {b.workspace && (
                          <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-600 font-medium truncate max-w-full">
                            {b.workspace}
                          </span>
                        )}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* ── Main area ── */}
      <div className="flex-1 flex flex-col min-w-0">

        {/* Top bar */}
        <div className="flex-shrink-0 bg-white border-b border-gray-200 px-5 h-[60px] flex items-center gap-4">
          {/* Target-mode toggle: agent vs flow */}
          <div className="inline-flex rounded-lg border border-gray-200 bg-white overflow-hidden">
            <button
              onClick={() => setTargetMode('agent')}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors ${
                targetMode === 'agent' ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'
              }`}
              title={t('chat.chatWithASingleAgent')}
            >
              <Bot className="w-3.5 h-3.5" />
              {t('chat.agent')}
            </button>
            <button
              onClick={() => setTargetMode('flow')}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors border-l border-gray-200 ${
                targetMode === 'flow' ? 'bg-emerald-50 text-emerald-700' : 'text-gray-500 hover:bg-gray-50'
              }`}
              title={t('chat.chatWithAFlowEach')}
            >
              <Workflow className="w-3.5 h-3.5" />
              {t('chat.flow')}
            </button>
            <button
              onClick={() => setTargetMode('team')}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors border-l border-gray-200 ${
                targetMode === 'team' ? 'bg-amber-50 text-amber-700' : 'text-gray-500 hover:bg-gray-50'
              }`}
              title={t('chat.handTheMessageToA')}
            >
              <UsersRound className="w-3.5 h-3.5" />
              {t('chat.team')}
            </button>
          </div>

          {targetMode === 'agent' ? (
            <AgentDropdown agents={selectableAgents} value={selectedAgent} onChange={(id) => {
              setSelectedAgent(id);
              // update current conv's agent
              if (currentConvId) {
                setConversations((prev) =>
                  prev.map((c) => c.id === currentConvId ? { ...c, agent_id: id } : c),
                );
              }
            }} />
          ) : targetMode === 'flow' ? (
            <FlowDropdown flows={flows} value={selectedFlow} onChange={(id) => {
              setSelectedFlow(id);
              if (currentConvId) {
                setConversations((prev) =>
                  prev.map((c) => c.id === currentConvId ? { ...c, flow_id: id, target_mode: 'flow' } : c),
                );
              }
            }} />
          ) : (
            <TeamDropdown teams={teams} value={selectedTeam} onChange={(id) => {
              setSelectedTeam(id);
              if (currentConvId) {
                setConversations((prev) =>
                  prev.map((c) => c.id === currentConvId ? { ...c, team_id: id, target_mode: 'team' } : c),
                );
              }
            }} />
          )}

          {/* The active chat's workspace is redundant when a specific workspace is
              selected in the header — only surface it in the default (all) view. */}
          {(!selectedWorkspace || selectedWorkspace === 'default') && (currentConv?.workspace || selectedWorkspace) && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <span className="font-semibold text-gray-400 uppercase tracking-wider text-[10px]">{t('chat.ws')}</span>
              <span className="font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded">
                {currentConv?.workspace || selectedWorkspace}
              </span>
            </div>
          )}

          {projects.length > 0 && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <FolderGit2 className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" />
              <select
                value={selectedProject}
                onChange={(e) => setSelectedProject(e.target.value)}
                className="text-xs border border-gray-200 rounded px-2 py-0.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-emerald-400 max-w-[140px]"
              >
                <option value="">{t('chat.noProject')}</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
          )}

          {selectedAgent && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <span className="font-semibold text-gray-400 uppercase tracking-wider text-[10px]">{t('chat.model')}</span>
              {agentProvider === 'inherit' ? (
                <span className="font-medium text-gray-400 bg-gray-100 px-2 py-0.5 rounded italic">{t('chat.global')}</span>
              ) : (
                <span className="font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded">
                  {agentProvider}{agentModel ? ` · ${agentModel}` : ''}
                </span>
              )}
            </div>
          )}

          {selectedWorkspace && selectableAgents.length === 0 && (
            <div className="flex items-center gap-2 bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-1.5 text-xs font-medium">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
              {t('chat.noAuthorizedAgentsInThis')}
            </div>
          )}


          {/* View-mode toggle: clean Chat vs full Build transcript + artifacts */}
          <div className="ml-auto inline-flex rounded-lg border border-gray-200 bg-white overflow-hidden">
            <button
              onClick={() => setViewMode('chat')}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors ${
                viewMode === 'chat' ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'
              }`}
              title={t('chat.cleanChatMessagesOnly')}
            >
              <MessageSquare className="w-3.5 h-3.5" />
              {t('chat.chat')}
            </button>
            <button
              onClick={() => setViewMode('build')}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors border-l border-gray-200 ${
                viewMode === 'build' ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'
              }`}
              title={t('chat.buildFullTranscriptToolsThinking')}
            >
              <Terminal className="w-3.5 h-3.5" />
              {t('chat.build')}
            </button>
          </div>

          {viewMode !== 'build' && (
            <button
              onClick={() => setProcessOpen((v) => !v)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50"
            >
              {processOpen ? (
                <>
                  <X className="w-3.5 h-3.5" />
                  {t('chat.hideProcess')}
                </>
              ) : (
                <>
                  <FileText className="w-3.5 h-3.5" />
                  {t('chat.showProcess')}
                </>
              )}
            </button>
          )}

          <div className="text-xs text-gray-400">
            {messages.length > 0 && `${messages.length} message${messages.length !== 1 ? 's' : ''}`}
          </div>
        </div>

        {/* Fixed Telegram debug-mirror banner — sits above the scrolling messages area */}
        {currentTelegramBinding && (
          <div className="flex-shrink-0 px-4 py-2 border-b border-sky-200 bg-sky-50 text-xs text-sky-800 flex items-center gap-2">
            <SendIcon className="w-3.5 h-3.5 shrink-0" />
            <div className="flex-1 min-w-0 truncate">
              <strong>{t('chat.telegram')}</strong> · {currentTelegramBinding.title || t('chat.telegramChat', { id: currentTelegramBinding.chat_id })}
              {' '}· {t('chat.boundTo')} <strong>{currentTelegramBinding.agent_name || currentTelegramBinding.agent_id}</strong>
              {currentTelegramBinding.workspace ? <> · {currentTelegramBinding.workspace}</> : null}
            </div>
            {!telegramReplyAllowed && (
              <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium" title={`Switch to "${currentTelegramBinding.workspace || 'default'}" to reply.`}>
                {t('chat.readOnly')}
              </span>
            )}
            <span className="px-1.5 py-0.5 rounded bg-sky-100 text-sky-700 font-medium">{t('chat.debugMirror')}</span>
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-full mx-auto px-6 py-8">
            {messages.length === 0 && !loading && (
              <div className="flex flex-col items-center justify-center h-full min-h-[40vh] text-center">
                {targetMode === 'team' ? (
                  <>
                    <div className="w-16 h-16 bg-amber-100 rounded-2xl flex items-center justify-center mb-5 shadow-sm">
                      <UsersRound className="w-8 h-8 text-amber-600" />
                    </div>
                    <h2 className="text-xl font-semibold text-gray-800 mb-2">
                      {teams.find((tm) => tm.team_id === selectedTeam)?.name || t('chat.selectATeam')}
                    </h2>
                    <p className="text-sm text-gray-500 max-w-sm leading-relaxed">
                      {t('chat.emptyTeam')}
                    </p>
                  </>
                ) : targetMode === 'flow' ? (
                  <>
                    <div className="w-16 h-16 bg-emerald-100 rounded-2xl flex items-center justify-center mb-5 shadow-sm">
                      <Workflow className="w-8 h-8 text-emerald-600" />
                    </div>
                    <h2 className="text-xl font-semibold text-gray-800 mb-2">
                      {flows.find((f) => f.id === selectedFlow)?.name || t('chat.selectAFlow')}
                    </h2>
                    <p className="text-sm text-gray-500 max-w-sm leading-relaxed">
                      {t('chat.emptyFlow')}
                    </p>
                  </>
                ) : (
                  <>
                    <div className="w-16 h-16 bg-indigo-100 rounded-2xl flex items-center justify-center mb-5 shadow-sm">
                      <Bot className="w-8 h-8 text-indigo-600" />
                    </div>
                    <h2 className="text-xl font-semibold text-gray-800 mb-2">
                      {agentName}
                    </h2>
                    <p className="text-sm text-gray-500 max-w-sm leading-relaxed">
                      {t('chat.emptyAgent')}
                      {selectedWorkspace && <> {t('chat.agentWorksIn')} <strong className="text-gray-700">{selectedWorkspace}</strong> {t('chat.workspace')}</>}
                    </p>
                  </>
                )}
              </div>
            )}

            {renderedMessages.map((msg, idx) => {
              const msgAgentName = msg.role !== 'user'
                ? (msg.agent_label || agents.find((a) => a.id === msg.agent_id)?.name || msg.agent_id || agentName)
                : undefined;
              // The mirrored turn is labelled: it is being written somewhere
              // else, so an answer appearing on its own is explained rather
              // than surprising.
              const liveLabel = msg.id === liveMessages[0]?.id ? (
                <div key="live-label" className="flex items-center gap-1.5 mb-2 text-[11px] font-medium text-indigo-500">
                  <Radio className="w-3 h-3 animate-pulse" />
                  {liveTurn?.source && liveTurn.source !== 'chat'
                    ? t('chat.liveFromSource', { source: liveTurn.source })
                    : t('chat.liveElsewhere')}
                </div>
              ) : null;
              if (viewMode === 'build') {
                // Reloaded messages lost their live timeline; fall back to the
                // server-reconstructed one (tools + thoughts) keyed by run_id.
                const reconstructed = (!msg.timeline || !msg.timeline.length) && msg.run_id
                  ? runTimelineByRunId[String(msg.run_id)]
                  : null;
                const buildMsg = reconstructed && reconstructed.length
                  ? { ...msg, timeline: reconstructed }
                  : msg;
                return (
                  <React.Fragment key={msg.id}>
                    {liveLabel}
                    <BuildMessage
                      msg={buildMsg}
                      agentName={msgAgentName}
                      onJumpArtifact={jumpToArtifact}
                    />
                  </React.Fragment>
                );
              }
              return (
                <React.Fragment key={msg.id}>
                  {liveLabel}
                  <MessageBubble
                    msg={msg}
                    isStreaming={
                      (loading && idx === renderedMessages.length - 1 && msg.role === 'agent')
                      || (msg.id === 'live-agent' && liveTurn?.status === 'running')
                    }
                    agentName={msgAgentName}
                    artifactsByPath={artifacts}
                    onAction={sendMessage}
                  />
                </React.Fragment>
              );
            })}

            {loading && (messages.length === 0 || messages[messages.length - 1].role !== 'agent') && (
              <TypingIndicator agentName={agentName} />
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input area */}
        <div className="flex-shrink-0 border-t border-gray-200 px-4 py-4">
          <div className="max-w-3xl mx-auto">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={onPickFiles}
            />
            {pendingReferences.length > 0 && (
              <div className="mb-2 flex flex-wrap items-center gap-1.5">
                {pendingReferences.map((ref) => (
                  <span
                    key={`${ref.kind}:${ref.id}`}
                    className="inline-flex items-center gap-1.5 max-w-full rounded-full border border-indigo-200
                      bg-indigo-50 px-2 py-1 text-[11px] text-indigo-700"
                    title={t(`chat.entityKind.${ref.kind}`, { defaultValue: ref.kind })}
                  >
                    <span aria-hidden="true">{ref.icon}</span>
                    {ref.url ? (
                      <Link to={ref.url} className="truncate max-w-[14rem] font-medium hover:underline">
                        {ref.label || ref.id}
                      </Link>
                    ) : (
                      <span className="truncate max-w-[14rem] font-medium">{ref.label || ref.id}</span>
                    )}
                    <button
                      type="button"
                      onClick={() => removeReference(ref.kind, ref.id)}
                      className="text-indigo-400 hover:text-red-600 flex-shrink-0"
                      title={t('chat.removeAttachment')}
                      disabled={loading}
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}

            {pendingAttachments.length > 0 && (
              <div className="mb-2 space-y-2">
                {pendingAttachments.map((att) => (
                  <div key={att.id} className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg border border-gray-200 bg-gray-50">
                    <div className="min-w-0">
                      <div className="text-xs text-gray-700 font-medium truncate">{att.filename}</div>
                      <div className="text-[11px] text-gray-500">{t('chat.bytes', { count: att.size })}</div>
                    </div>
                    <div className="flex items-center gap-3">
                      <label className={`flex items-center gap-1.5 text-xs ${selectedWorkspace ? 'text-gray-600' : 'text-gray-400'}`}>
                        <input
                          type="checkbox"
                          checked={Boolean(att.store_to_workspace)}
                          onChange={(e) => toggleAttachmentStore(att.id, e.target.checked)}
                          disabled={!selectedWorkspace || loading}
                        />
                        {t('chat.storeInWorkspace')}
                      </label>
                      <button
                        type="button"
                        onClick={() => removeAttachment(att.id)}
                        className="text-gray-400 hover:text-red-600"
                        title={t('chat.removeAttachment')}
                        disabled={loading}
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                ))}
                {!selectedWorkspace && (
                  <p className="text-[11px] text-amber-600">{t('chat.selectAWorkspaceIfYou')}</p>
                )}
              </div>
            )}

            {/* Slash command picker */}
            {commandMenuOpen && commandSuggestions.length > 0 && (
              <div className="mb-2 bg-white border border-gray-200 rounded-xl shadow-lg overflow-hidden">
                <div className="px-3 py-1.5 bg-gray-50 border-b border-gray-100 flex items-center gap-1.5">
                  <Terminal className="w-3 h-3 text-indigo-500" />
                  <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">{t('chat.commandsLabel')}</span>
                </div>
                {commandSuggestions.map((cmd, idx) => (
                  <button
                    key={cmd.name}
                    type="button"
                    onMouseDown={(e) => { e.preventDefault(); selectCommand(cmd); }}
                    className={`w-full flex items-start gap-3 px-3 py-2 text-left transition-colors ${
                      idx === commandMenuIndex ? 'bg-indigo-50' : 'hover:bg-gray-50'
                    }`}
                  >
                    <span className=" text-sm font-semibold text-indigo-600 shrink-0">{cmd.name}</span>
                    <span className="text-xs text-gray-500 mt-0.5">{cmd.descriptionKey ? t(cmd.descriptionKey) : cmd.description}</span>
                  </button>
                ))}
              </div>
            )}

            {/* What is left of the model's window, read where the next message
                is typed. Clearing is offered once the context is tight; /clear
                is the same action from the keyboard. */}
            <ContextMeter
              usage={contextUsage}
              onClear={loading ? null : () => selectCommand({ name: '/clear' })}
              className="mb-2 px-1"
            />

            <div
              className="flex items-center gap-3 border border-gray-300 rounded-2xl px-4 py-3
                focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-100
                shadow-sm transition-all"
            >
              {/* Attach: a file from the computer, or a record the hub already
                  holds (task / view / project / scenario / loop / …). An entity
                  picked here is folded into the prompt whether or not the agent
                  owns a tool that could have fetched it. */}
              <div className="relative flex-shrink-0">
                <button
                  type="button"
                  onClick={() => setAttachMenuOpen((open) => !open)}
                  className="w-8 h-8 flex items-center justify-center rounded-full bg-gray-100 text-gray-600 hover:bg-gray-200 transition-colors disabled:opacity-40"
                  title={t('chat.attachMenuTitle')}
                  disabled={loading || !hasTarget}
                >
                  <Paperclip className="w-4 h-4" />
                </button>

                {attachMenuOpen && (
                  <>
                    {/* Click-away layer: a menu anchored above the composer has
                        no other way to close without stealing focus. */}
                    <div className="fixed inset-0 z-40" onClick={() => setAttachMenuOpen(false)} />
                    <div className="absolute bottom-10 left-0 z-50 w-60 max-h-80 overflow-y-auto bg-white border border-gray-200 rounded-xl shadow-lg py-1">
                      <button
                        type="button"
                        onClick={() => { setAttachMenuOpen(false); fileInputRef.current?.click(); }}
                        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
                      >
                        <Upload className="w-4 h-4 text-gray-400" />
                        {t('chat.attachFiles')}
                      </button>
                      {contextKinds.length > 0 && (
                        <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wide text-gray-400 border-t border-gray-100 mt-1">
                          {t('chat.attachFromHub')}
                        </div>
                      )}
                      {contextKinds.map((k) => (
                        <button
                          key={k.kind}
                          type="button"
                          onClick={() => { setAttachMenuOpen(false); setPickerKind(k.kind); }}
                          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
                        >
                          <span aria-hidden="true">{k.icon}</span>
                          {t(`chat.entityKind.${k.kind}`, { defaultValue: k.noun })}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>

              <textarea
                ref={textareaRef}
                className="flex-1 resize-none text-base text-gray-800 placeholder-gray-400 focus:outline-none bg-transparent leading-relaxed disabled:opacity-50"
                placeholder={composerPlaceholder}
                rows={1}
                value={input}
                disabled={loading || !hasTarget}
                onChange={(e) => {
                  const val = e.target.value;
                  setInput(val);
                  resizeTextarea();
                  setCommandMenuOpen(val.startsWith('/'));
                  setCommandMenuIndex(0);
                }}
                onKeyDown={handleKeyDown}
              />

              {loading ? (
                <button
                  onClick={stopGeneration}
                  title={t('chat.stop')}
                  className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-red-100 text-red-600 hover:bg-red-200 transition-colors"
                >
                  <StopCircle className="w-4 h-4" />
                </button>
              ) : currentTelegramBinding ? (
                <button
                  onClick={sendAsBot}
                  disabled={!input.trim() || telegramSending || !telegramReplyAllowed}
                  title={!telegramReplyAllowed
                    ? t('chat.telegramBoundElsewhere', { workspace: currentTelegramBinding.workspace || 'default' })
                    : t('chat.sendAsBotToChat')}
                  className="flex-shrink-0 h-8 px-3 flex items-center gap-1.5 rounded-full
                    bg-sky-600 text-white hover:bg-sky-700 text-xs font-semibold
                    disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  <SendIcon className="w-3.5 h-3.5" />
                  {telegramSending ? t('chat.sending') : t('chat.sendAsBot')}
                </button>
              ) : (
                <button
                  onClick={sendMessage}
                  disabled={(!input.trim() && pendingAttachments.length === 0 && pendingReferences.length === 0) || !hasTarget}
                  title={t('chat.sendEnter')}
                  className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full
                    bg-indigo-600 text-white hover:bg-indigo-700
                    disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  <Send className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
            {telegramError && (
              <p className="text-xs text-red-600 mt-2">{telegramError}</p>
            )}
            {attachmentError && (
              <p className="text-xs text-red-600 mt-2">{attachmentError}</p>
            )}

            {pickerKind && (
              <ContextEntityPicker
                initialKind={pickerKind}
                workspace={selectedWorkspace}
                projectId={selectedProject}
                selected={pendingReferences}
                onAdd={addReferences}
                onClose={() => setPickerKind(null)}
              />
            )}
            <p className="text-center text-xs text-gray-400 mt-2">
              {t('chat.composerHint')} <span className="">/</span> {t('chat.forCommands')}
            </p>
          </div>
        </div>
      </div>

      {/* ── Side panel ──
          Build view: Artifacts only, always open (file diffs live here; steps and
          tool calls are shown inline in the transcript, so there is no Process tab).
          Chat view: the legacy Agent Process panel, toggled by "Show process". */}
      {(() => {
        const isBuild = viewMode === 'build';
        const panelVisible = isBuild || processOpen;
        if (!panelVisible) return null;
        return (
          <div className={`${isBuild ? 'w-[560px]' : 'w-[420px]'} flex-shrink-0 bg-white border-l border-gray-200 flex flex-col`}>
            <div className="px-4 h-[60px] border-b border-gray-200 flex items-center justify-between">
              {isBuild ? (
                <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
                  <FileText className="w-4 h-4 text-indigo-500" />
                  {t('chat.artifacts')}
                </h3>
              ) : (
                <div>
                  <h3 className="text-sm font-semibold text-gray-800">{t('chat.agentProcess')}</h3>
                  {processInsights?.session_id && (
                    <p className="text-[10px] text-gray-500 -mb-0.5">
                      Session:{' '}
                      <a
                        href={`/sessions/${processInsights.session_id}`}
                        className="text-indigo-600 hover:text-indigo-700 hover:underline"
                      >
                        {processInsights.session_id}
                      </a>
                    </p>
                  )}
                </div>
              )}
              {!isBuild && (
                <div className="flex items-center gap-1">
                  {activeRunId && (
                    <button
                      onClick={() => loadProcessData(activeRunId)}
                      className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded"
                      title={t('chat.refreshProcess')}
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                  )}
                  <button
                    onClick={() => setProcessOpen(false)}
                    className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
                    title={t('chat.closePanel')}
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              )}
            </div>

            {isBuild ? (
              <ArtifactsPanel artifacts={artifacts} />
            ) : !activeRunId ? (
              <div className="p-4 text-sm text-gray-500">
                {t('chat.sendAMessageThenOpen')}
              </div>
            ) : processLoading ? (
              <div className="flex items-center gap-2 text-sm text-gray-500 p-4">
                <RefreshCw className="w-4 h-4 animate-spin text-indigo-500" />
                {t('chat.loadingProcessDetails')}
              </div>
            ) : processError ? (
              <div className="p-4 text-sm text-red-600">{processError}</div>
            ) : (
              <ProcessPanelContent
                processInsights={processInsights}
              />
            )}
          </div>
        );
      })()}
    </div>
  );
}
