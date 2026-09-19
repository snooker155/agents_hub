import React, { useState } from 'react';
import {
  PlusCircle, MinusCircle, Pencil, MoreHorizontal, Wrench, Brain,
  AlertTriangle, CheckCircle2, ChevronDown, ChevronUp,
} from 'lucide-react';
import { toolInline } from '../toolFormatters';
import { trimBubbleText } from '../../lib/chatText';
import { useI18n } from '../../i18n';

// How a change to the entity under edit reads: the verb decides the icon and
// the colour, so a run of them can be skimmed for what was *removed* without
// reading a word.
const CHANGE_STYLES = {
  added: { icon: PlusCircle, className: 'text-green-600' },
  removed: { icon: MinusCircle, className: 'text-red-500' },
  updated: { icon: Pencil, className: 'text-sky-600' },
  set: { icon: Pencil, className: 'text-sky-600' },
  more: { icon: MoreHorizontal, className: 'text-gray-400' },
};

/**
 * A finished thought, folded away.
 *
 * Reasoning is the longest thing in the feed and the least often read: a turn
 * with three thoughts in it pushes what the agent actually *did* off the top of
 * the panel. So it reads as one line — the opening words of the thought, enough
 * to recognise it by — and opens in place when that line is worth reading in
 * full. The same trade the main chat makes with its Thought card.
 */
function ThoughtStep({ text }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const body = String(text || '');
  if (!body.trim()) return null;
  const preview = body.replace(/\s+/g, ' ').trim();
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 text-left text-[11px] text-gray-400 hover:text-gray-600"
      >
        <Brain className="w-3.5 h-3.5 text-amber-400 shrink-0" />
        <span className="font-medium shrink-0">{t('flowChatFeed.thought')}</span>
        {!open && <span className="italic truncate flex-1 min-w-0">{preview}</span>}
        {open
          ? <ChevronUp className="w-3 h-3 ml-auto shrink-0" />
          : <ChevronDown className="w-3 h-3 shrink-0" />}
      </button>
      {open && (
        <div className="mt-1 text-[11px] text-gray-400 italic whitespace-pre-wrap break-words">
          {body}
        </div>
      )}
    </div>
  );
}

// One item in an agent chat feed (user/assistant turns + thinking + tool/graph
// steps + the entity changes a build chat makes). Shared by the architect chat
// (ProjectGraph), the planner chat and every entity build chat.
export function FeedItem({ e }) {
  const { t } = useI18n();
  if (e.k === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg rounded-br-sm bg-indigo-600 text-white text-xs px-3 py-2 whitespace-pre-wrap">{trimBubbleText(e.text)}</div>
      </div>
    );
  }
  // No avatar: there is exactly one agent in an entity build chat and the
  // bubble's side already says who is speaking, so a robot head on every reply
  // only bought a 1.5rem gutter that every step below then had to reserve.
  if (e.k === 'assistant') {
    return (
      <div className="flex justify-start">
        <div className="max-w-[92%] rounded-lg rounded-bl-sm bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-200 text-xs px-3 py-2 whitespace-pre-wrap">
          {trimBubbleText(e.text)}
          {e.live ? <span className="inline-block w-1.5 h-3 ml-0.5 -mb-0.5 bg-violet-400 animate-pulse" /> : null}
        </div>
      </div>
    );
  }
  if (e.k === 'node') {
    return (
      <div className="flex items-center gap-1.5 text-[11px] text-green-600">
        <PlusCircle className="w-3.5 h-3.5 shrink-0" /> {t('flowChatFeed.added')} <span className="font-medium">{e.label}</span>
        {e.edge ? <span className="text-gray-400">· {e.edge}</span> : null}
      </div>
    );
  }
  // What the agent just changed in the thing the chat is pinned to — the same
  // edit the panel beside the chat is showing at this moment, written down so
  // the conversation says who did it and when.
  if (e.k === 'entity') {
    const style = CHANGE_STYLES[e.action] || CHANGE_STYLES.updated;
    const Icon = style.icon;
    if (e.action === 'more') {
      return (
        <div className="flex items-center gap-1.5 text-[11px] text-gray-400">
          <Icon className="w-3.5 h-3.5 shrink-0" /> {t('flowChatFeed.moreChanges', { count: Number(e.label) || 0 })}
        </div>
      );
    }
    const kind = e.kind ? t(`flowChatFeed.kinds.${e.kind}`, { defaultValue: e.kind }) : '';
    return (
      <div className={`flex items-start gap-1.5 text-[11px] ${style.className}`}>
        <Icon className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <div className="min-w-0">
          {t(`flowChatFeed.change.${e.action}`, { defaultValue: e.action })}
          {kind ? <span className="text-gray-400"> {kind}</span> : null}
          {' '}
          <span className="font-medium break-words">{e.label}</span>
        </div>
      </div>
    );
  }
  if (e.k === 'tool') {
    // The arguments, summarised: "modify_world_tool · wld_7c1a" is a step you
    // can follow, where a bare tool name repeated eight times is not.
    const inline = e.input ? toolInline(e.tool, e.input, 60) : '';
    const failed = e.status === 'error';
    return (
      <div className={`flex items-start gap-1.5 text-[11px] ${failed ? 'text-red-600' : 'text-gray-500'}`}>
        {failed
          ? <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          : e.status === 'done'
            ? <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 text-green-500 shrink-0" />
            : <Wrench className="w-3.5 h-3.5 mt-0.5 text-indigo-400 shrink-0" />}
        <div className="min-w-0">
          <span className="font-mono">{e.tool}</span>
          {inline ? <span className="text-gray-400"> · {inline}</span> : null}
          {e.status === 'running' ? <span className="text-indigo-400 animate-pulse"> …</span> : null}
          {failed && e.error ? <span className="break-words"> — {e.error}</span> : null}
        </div>
      </div>
    );
  }
  if (e.k === 'thinking') return <ThoughtStep text={e.text} />;
  if (e.k === 'error') {
    return (
      <div className="flex items-start gap-1.5 text-[11px] text-red-600">
        <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {e.text}
      </div>
    );
  }
  return null;
}

export default FeedItem;
