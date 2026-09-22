/**
 * The parts a message bubble can carry besides its text: the buttons of a
 * structured response, the files a turn touched, and the hub records attached to it.
 */
import { useI18n } from '../../i18n';
import { ArtifactItem } from './panels';
import { ArrowUpRight, ChevronDown, ChevronUp, FileText, Link2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

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

export { ResponseButtons, MessageFiles, MessageEntities };
