import { ArrowRightLeft, AtSign, Bot } from 'lucide-react';

/**
 * The `@agent` list over a composer (useAgentMention.js), drawn as the slash
 * command list is: a head, then one row per agent, the one the arrows are on
 * marked. A row is picked on mouse down, so the box keeps its focus.
 */
export function MentionMenu({ mention, label, hint }) {
  if (!mention?.open) return null;
  return (
    <div className="mb-2 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-lg" role="listbox" data-testid="mention-menu">
      <div className="flex items-center gap-1.5 border-b border-gray-100 bg-gray-50 px-3 py-1.5">
        <AtSign className="h-3 w-3 text-indigo-500" />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">{label}</span>
        {hint && <span className="ml-auto truncate text-[11px] text-gray-400">{hint}</span>}
      </div>
      {mention.items.map((agent, idx) => (
        <button
          key={agent.id}
          type="button"
          role="option"
          aria-selected={idx === mention.index}
          onMouseEnter={() => mention.setIndex(idx)}
          onMouseDown={(e) => { e.preventDefault(); mention.pick(agent); }}
          className={`flex w-full items-center gap-3 px-3 py-2 text-left transition-colors ${
            idx === mention.index ? 'bg-indigo-50' : 'hover:bg-gray-50'
          }`}
        >
          <Bot className="h-4 w-4 shrink-0 text-indigo-500" />
          <span className="min-w-0 truncate text-sm font-semibold text-gray-800">{agent.name || agent.id}</span>
          <span className="shrink-0 font-mono text-[11px] text-gray-400">@{agent.id}</span>
          {agent.description && (
            <span className="min-w-0 flex-1 truncate text-right text-xs text-gray-500">{agent.description}</span>
          )}
        </button>
      ))}
    </div>
  );
}

/**
 * Above the box while its text names an agent: whom the message goes to.
 */
export function MentionChip({ agent, label }) {
  if (!agent) return null;
  return (
    <div className="mb-2 flex" data-testid="mention-chip">
      <span className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-0.5 text-[11px] font-medium text-indigo-700">
        <ArrowRightLeft className="h-3 w-3 shrink-0" />
        <span className="truncate">{label}</span>
      </span>
    </div>
  );
}

export default MentionMenu;
