/**
 * The parts a message bubble can carry besides its text: the buttons of a
 * structured response, the files a turn touched, and the hub records attached to it.
 */
import { useI18n } from '../../i18n';
import ViewCard from '../../views/ViewCard';
import { useContext } from 'react';
import { fileLink } from './artifactOps';
import { FileOpenContext } from './fileOpenContext';
import { ArrowUpRight, FileText, Link2 } from 'lucide-react';
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

// Files the agent created, edited or deleted during this turn, shown inside
// the reply as small blocks, each one the file's name linked to its page (the
// Artifacts page, or the Files tab of its workspace). The link rides on the
// change itself (`url`, set by the filesystem tools, see
// common/entity_links.py file_url); a change recorded before that finds its
// link among the turn's entity links by path. A deleted file has no page left
// and is shown struck through, without one. What changed inside a file is the
// side panel's Diff, not the reply's.
function FileLinks({ files, entities, className = 'mt-2' }) {
  const { t } = useI18n();
  // A page with the run's artifacts beside the chat shows the file there.
  const openFile = useContext(FileOpenContext);
  const items = [];
  const seen = new Set();
  for (const f of files || []) {
    if (!f?.path || seen.has(f.path)) continue;
    seen.add(f.path);
    items.push(f);
  }
  if (!items.length) return null;
  return (
    <div className={`${className} flex flex-wrap gap-1.5`} data-testid="message-files">
      {items.map((f) => {
        const url = openFile && f.op !== 'delete' ? null : fileLink(f, entities);
        const inPanel = Boolean(openFile) && f.op !== 'delete';
        const name = f.path.split('/').pop() || f.path;
        // A button, plainly: a brand-coloured frame, raised, its icon in the
        // brand colour; hover only fills it. A file with nowhere to go
        // (deleted) stays a flat label.
        const active = Boolean(url || inPanel);
        const cls = `inline-flex max-w-full items-center gap-1.5 rounded-lg border px-2.5 py-1 no-underline ${active
          ? 'border-indigo-300 bg-white shadow-sm transition-colors hover:bg-indigo-50'
          : 'border-gray-200 bg-gray-50'}`;
        const body = (
          <>
            <FileText className={`h-3.5 w-3.5 flex-shrink-0 ${active ? 'text-indigo-500' : 'text-gray-400'}`} />
            <span className={`truncate text-xs ${active ? 'font-medium text-gray-800' : 'text-gray-500'} ${f.op === 'delete' ? 'line-through' : ''}`}>
              {name}
            </span>
          </>
        );
        if (inPanel) {
          return (
            <button key={f.path} type="button" onClick={() => openFile(f.path, f)} className={`${cls} text-left`} title={`${t('chat.showInArtifacts')}: ${f.path}`}>
              {body}
            </button>
          );
        }
        return url ? (
          <Link key={f.path} to={url} className={cls} title={`${t('chat.openFile')}: ${f.path}`}>{body}</Link>
        ) : (
          <span key={f.path} className={cls} title={f.path}>{body}</span>
        );
      })}
    </div>
  );
}

// The files field at the foot of a bubble: under a dividing line, so the
// files read apart from the answer above them. Nothing when the turn touched
// no file.
function MessageFiles({ files, entities, className = '' }) {
  if (!(files || []).some((f) => f?.path)) return null;
  return (
    <div className={`${className} border-t border-gray-200`} data-testid="message-files-field">
      <FileLinks files={files} entities={entities} className="" />
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

// Each view as a live preview in the reply: the card renders the view, opens it
// full size in a modal, and links to the view's own page.
function MessageViews({ views }) {
  if (!views?.length) return null;
  return (
    <div className="space-y-2" data-testid="message-views">
      {views.map((v) => <ViewCard key={v.view_id} viewRef={v} inChat />)}
    </div>
  );
}

export { ResponseButtons, FileLinks, MessageFiles, MessageEntities, MessageViews };
