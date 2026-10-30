/**
 * The chat's agent picker: the top bar's button, and a panel in two halves.
 *
 * On the left, a search over the agents and a scope (all, own, system),
 * then the agents in sections (see agentPickerModel). On the
 * right, the agent under the pointer or the arrow keys: what it does, its
 * badges, how many tools and delegates it has, what it remembers with (its
 * memory pools and personal memory), its tools and commands, a link to its page and a button that picks it. On a phone the
 * right half is left out and the panel is the list alone.
 *
 * Arrow keys move through the list, Enter picks, Escape or a click outside
 * closes.
 *
 * The same picker serves a form (variant "field", the Plan's job form): the
 * button is a full width field, the panel opens over the page centred rather
 * than under the button (a dialog has no room below), the form may offer a
 * row for no agent at all, and the counts are whatever the page counts.
 * A form with no use for the right half (the workspace secrets) turns it off
 * with `details={false}`: the panel is then the list alone, as on a phone.
 *
 * `placement="up"` opens the bar's panel above the button, for a picker at
 * the bottom of the screen (an instance's message box).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Bot, Check, ChevronDown, Database, ExternalLink, GitBranch, Lock, Search, Sparkles, Star, UserRound } from 'lucide-react';
import { useI18n } from '../../i18n';
import {
  avatarTone, initials, pickerSections, recentAgentIds,
} from './agentPickerModel';

const SCOPES = ['all', 'own', 'system'];
const TOOL_CHIPS = 12;

function Avatar({ agent, size = 'w-7 h-7 text-[10px]' }) {
  return (
    <span className={`${size} shrink-0 rounded-lg flex items-center justify-center font-semibold ${avatarTone(agent.id)}`}>
      {initials(agent)}
    </span>
  );
}

function Badge({ tone = 'bg-gray-100 text-gray-600', children, title }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${tone}`}>
      {children}
    </span>
  );
}

function Fact({ label, value }) {
  return (
    <div className="rounded-lg bg-gray-50 px-2.5 py-1.5">
      <div className="text-[10px] uppercase tracking-wider text-gray-400">{label}</div>
      <div className="text-sm font-semibold text-gray-800 tabular-nums truncate">{value}</div>
    </div>
  );
}

/** What the agent remembers with in this workspace (routes/agents/listing.py):
 * its memory pools by name, read-only ones marked, and personal memory. */
function AgentMemory({ memory, t }) {
  if (!memory) return null;
  const pools = memory.pools || [];
  return (
    <div data-testid="agent-picker-memory">
      <div className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">{t('chat.agentPicker.memoryTitle')}</div>
      <div className="flex flex-wrap gap-1">
        {pools.map((pool) => (
          <Badge key={pool.id} tone="bg-indigo-50 text-indigo-700"
                 title={pool.read_only ? t('chat.agentPicker.memoryReadOnly') : undefined}>
            <Database className="w-3 h-3" /> {pool.name || String(pool.id).slice(0, 8)}
            {pool.read_only && <Lock className="w-3 h-3" />}
          </Badge>
        ))}
        <Badge tone={memory.personal ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-500'}>
          <UserRound className="w-3 h-3" />
          {memory.personal ? t('chat.agentPicker.personalOn') : t('chat.agentPicker.personalOff')}
        </Badge>
      </div>
      {!pools.length && (
        <div className="text-[11px] text-gray-400 mt-1">{t('chat.agentPicker.noPools')}</div>
      )}
    </div>
  );
}

/** The right half: everything worth knowing about one agent before talking to it. */
function AgentDetails({ agent, count, countLabel, pickLabel, currentLabel, selected, onPick, t }) {
  const tools = agent.tools || [];
  const commands = agent.commands || [];
  return (
    <div className="flex flex-col h-full min-h-0" data-testid="agent-picker-details">
      <div className="flex items-start gap-3 px-4 pt-4">
        <Avatar agent={agent} size="w-10 h-10 text-sm" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900 truncate">{agent.name || agent.id}</div>
          <div className="text-[11px] font-mono text-gray-400 truncate">{agent.id}</div>
        </div>
      </div>

      <div className="flex flex-wrap gap-1 px-4 pt-2">
        {agent.is_default_chat_agent && (
          <Badge tone="bg-amber-50 text-amber-700"><Star className="w-3 h-3" /> {t('chat.agentPicker.default')}</Badge>
        )}
        <Badge tone={agent.system ? 'bg-sky-50 text-sky-700' : 'bg-emerald-50 text-emerald-700'}>
          {agent.system ? t('chat.agentPicker.systemBadge') : t('chat.agentPicker.ownBadge')}
        </Badge>
        {agent.shared && <Badge>{t('chat.agentPicker.shared')}</Badge>}
        {agent.remote && <Badge tone="bg-violet-50 text-violet-700">{t('chat.agentPicker.remote')}</Badge>}
        {agent.has_running_node && (
          <Badge tone="bg-emerald-50 text-emerald-700">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> {t('chat.agentPicker.running')}
          </Badge>
        )}
        {agent.extends && (
          <Badge title={t('chat.agentPicker.extendsTitle', { parent: agent.extends })}>
            <GitBranch className="w-3 h-3" /> {agent.extends}
          </Badge>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
        <p className="text-xs leading-relaxed text-gray-600 whitespace-pre-line">
          {agent.description || <span className="text-gray-400">{t('chat.agentPicker.noDescription')}</span>}
        </p>

        <div className={`grid gap-1.5 ${countLabel ? 'grid-cols-3' : 'grid-cols-2'}`}>
          <Fact label={t('chat.agentPicker.tools')} value={tools.length} />
          <Fact label={t('chat.agentPicker.delegates')} value={(agent.delegates || []).length} />
          {countLabel && <Fact label={countLabel} value={count} />}
        </div>

        <AgentMemory memory={agent.memory} t={t} />

        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-gray-500">
          <span>{agent.skills_enabled ? t('chat.agentPicker.skillsOn') : t('chat.agentPicker.skillsOff')}</span>
          {agent.current_version ? <span>{t('chat.agentPicker.version', { n: agent.current_version })}</span> : null}
        </div>

        {tools.length > 0 && (
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">{t('chat.agentPicker.tools')}</div>
            <div className="flex flex-wrap gap-1">
              {tools.slice(0, TOOL_CHIPS).map((tool) => (
                <span key={tool} className="px-1.5 py-0.5 rounded bg-gray-100 text-[10px] font-mono text-gray-600">{tool}</span>
              ))}
              {tools.length > TOOL_CHIPS && (
                <span className="px-1.5 py-0.5 text-[10px] text-gray-400">
                  {t('chat.agentPicker.more', { n: tools.length - TOOL_CHIPS })}
                </span>
              )}
            </div>
          </div>
        )}

        {commands.length > 0 && (
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">{t('chat.agentPicker.commands')}</div>
            <div className="flex flex-wrap gap-1">
              {commands.map((c) => (
                <span key={c.name || c} title={c.description || undefined}
                      className="px-1.5 py-0.5 rounded bg-indigo-50 text-[10px] font-mono text-indigo-700">
                  /{String(c.name || c).replace(/^\//, '')}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 px-4 py-3 border-t border-gray-100">
        <Link
          to={`/agents/${encodeURIComponent(agent.id)}`}
          className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-gray-800"
        >
          <ExternalLink className="w-3.5 h-3.5" /> {t('chat.agentPicker.openPage')}
        </Link>
        <button
          type="button"
          onClick={onPick}
          disabled={selected}
          className="ml-auto inline-flex items-center gap-1.5 h-8 px-3 rounded-lg bg-indigo-600 text-white text-xs font-medium hover:bg-indigo-700 disabled:bg-gray-100 disabled:text-gray-400"
        >
          {selected ? <><Check className="w-3.5 h-3.5" /> {currentLabel}</> : pickLabel}
        </button>
      </div>
    </div>
  );
}

/** The right half for the no agent row: what picking nobody means. */
function NoneDetails({ option, selected, onPick, pickLabel, currentLabel }) {
  return (
    <div className="flex flex-col h-full min-h-0" data-testid="agent-picker-details">
      <div className="flex items-start gap-3 px-4 pt-4">
        <span className="w-10 h-10 shrink-0 rounded-lg flex items-center justify-center bg-gray-100 text-gray-500">
          <Sparkles className="w-5 h-5" />
        </span>
        <div className="min-w-0 flex-1 text-sm font-semibold text-gray-900">{option.label}</div>
      </div>
      <p className="flex-1 min-h-0 overflow-y-auto px-4 py-3 text-xs leading-relaxed text-gray-600 whitespace-pre-line">
        {option.hint}
      </p>
      <div className="flex items-center px-4 py-3 border-t border-gray-100">
        <button
          type="button"
          onClick={onPick}
          disabled={selected}
          className="ml-auto inline-flex items-center gap-1.5 h-8 px-3 rounded-lg bg-indigo-600 text-white text-xs font-medium hover:bg-indigo-700 disabled:bg-gray-100 disabled:text-gray-400"
        >
          {selected ? <><Check className="w-3.5 h-3.5" /> {currentLabel}</> : pickLabel}
        </button>
      </div>
    </div>
  );
}

const NONE = { id: '', none: true };

/**
 * @param {object} props
 * @param {object[]} props.agents          the agents this workspace offers.
 * @param {string} props.value             the picked id.
 * @param {function} props.onChange
 * @param {object[]} [props.conversations] for the recent section.
 * @param {'bar'|'field'} [props.variant]  the chat's top bar, or a form field.
 * @param {string} [props.placeholder]     the button's text with nothing picked.
 * @param {{label: string, hint?: string}} [props.noneOption] a first row that picks '' (no agent).
 * @param {string[]} [props.recent]        the recent section, in place of the conversations'.
 * @param {Map<string, number>} [props.counts] a per agent count the page keeps (its jobs, its deployments), shown with labels.count.
 * @param {object} [props.labels]          overrides: recent (section), count, pick, current.
 * @param {boolean} [props.details]        the right half with the agent's card (on by default).
 */
export default function AgentPicker({
  agents, value, onChange, conversations = [], variant = 'bar', placeholder = '', noneOption = null,
  recent: recentIds = null, counts: countsIn = null, labels = {}, details = true, placement = 'down',
}) {
  const { t } = useI18n();
  const isField = variant === 'field';
  const text = {
    recent: labels.recent || t('chat.agentPicker.section.recent'),
    count: countsIn ? labels.count || '' : '',
    pick: labels.pick || t('chat.agentPicker.pick'),
    current: labels.current || t('chat.agentPicker.current'),
  };
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [scope, setScope] = useState('all');
  const [activeId, setActiveId] = useState(null);
  const rootRef = useRef(null);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const selected = agents.find((a) => a.id === value) || null;
  const recent = useMemo(() => recentIds || recentAgentIds(conversations), [recentIds, conversations]);
  const scopeCounts = useMemo(() => ({
    all: agents.length,
    own: agents.filter((a) => !a.system).length,
    system: agents.filter((a) => a.system).length,
  }), [agents]);
  const sections = useMemo(
    () => pickerSections(agents, { query, scope, recent }),
    [agents, query, scope, recent],
  );
  // The no agent row leads the list while nothing is searched for.
  const showNone = !!noneOption && !query.trim();
  const flat = useMemo(
    () => [...(showNone ? [NONE] : []), ...sections.flatMap((s) => s.items)],
    [sections, showNone],
  );
  const active = flat.find((a) => a.id === activeId) || flat[0] || null;

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  // Keep the highlighted row in view while arrowing through a long list.
  useEffect(() => {
    if (!open || !active) return;
    const row = listRef.current?.querySelector(`[data-agent-id="${CSS.escape(active.id)}"]`);
    if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  const openPanel = () => {
    setQuery('');
    setActiveId(value || (noneOption ? '' : null));
    setOpen(true);
    queueMicrotask(() => inputRef.current?.focus());
  };

  const pick = (agent) => {
    if (!agent) return;
    setOpen(false);
    if (agent.id !== value) onChange(agent.id);
  };

  const move = (step) => {
    if (!flat.length) return;
    const i = Math.max(0, flat.findIndex((a) => a.id === active?.id));
    setActiveId(flat[Math.min(flat.length - 1, Math.max(0, i + step))].id);
  };

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(active); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setOpen(false); }
  };

  const chip = (on) => `shrink-0 h-6 px-2 rounded-md text-[11px] font-medium transition-colors ${
    on ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-100'
  }`;

  const emptyText = (!value && noneOption?.label) || placeholder || t('chat.selectAgent');
  const toggle = () => (open ? setOpen(false) : openPanel());

  return (
    <div className="relative" ref={rootRef}>
      {isField ? (
        <button
          type="button"
          data-testid="agent-picker"
          onClick={toggle}
          aria-haspopup="listbox"
          aria-expanded={open}
          className="w-full flex items-center gap-2.5 border border-gray-200 rounded-lg px-2 py-1.5 text-left bg-white hover:bg-gray-50 transition-colors"
        >
          {selected
            ? <Avatar agent={selected} />
            : (
              <span className="w-7 h-7 shrink-0 rounded-lg flex items-center justify-center bg-gray-100 text-gray-400">
                {!value && noneOption ? <Sparkles className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
              </span>
            )}
          <span className="min-w-0 flex-1">
            <span className={`block text-sm truncate ${selected ? 'text-gray-900' : 'text-gray-500'}`}>
              {selected ? (selected.name || selected.id) : emptyText}
            </span>
            {selected && (
              <span className="block text-[11px] text-gray-400 truncate">
                {t('chat.agentPicker.toolsCount', { count: (selected.tools || []).length })}
              </span>
            )}
          </span>
          {selected?.has_running_node && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />}
          <ChevronDown className={`w-4 h-4 text-gray-400 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
      ) : (
        <button
          type="button"
          data-testid="agent-picker"
          onClick={toggle}
          aria-haspopup="listbox"
          aria-expanded={open}
          className="h-8 flex items-center gap-1.5 pl-1 pr-2.5 rounded-lg border border-gray-200 bg-white text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors max-w-[260px]"
        >
          {selected
            ? <Avatar agent={selected} size="w-6 h-6 text-[9px]" />
            : <Bot className="w-4 h-4 ml-1 shrink-0 text-indigo-500" />}
          <span className={`truncate ${selected ? '' : 'text-gray-500'}`}>{selected?.name || emptyText}</span>
          {selected?.has_running_node && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />}
          <ChevronDown className={`w-3.5 h-3.5 text-gray-400 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
      )}

      {open && isField && (
        // Inside the root, so the outside click check counts it as the picker's.
        <div className="fixed inset-0 z-[60] bg-black/20" onMouseDown={() => setOpen(false)} />
      )}
      {open && (
        <div
          data-testid="agent-picker-panel"
          className={`${isField
            ? 'fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[61]'
            : placement === 'up' ? 'absolute bottom-full left-0 mb-1 z-50' : 'absolute top-full left-0 mt-1 z-50'} ${details
            ? 'w-[min(660px,calc(100vw-1.5rem))]'
            : 'w-[min(360px,calc(100vw-1.5rem))]'} h-[min(460px,70vh)] flex bg-white border border-gray-200 rounded-xl shadow-xl overflow-hidden`}
        >
          <div className={`flex flex-col min-w-0 w-full ${details ? 'sm:w-[290px] sm:border-r border-gray-100' : ''}`}>
            <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-100">
              <Search className="w-4 h-4 text-gray-400 shrink-0" />
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => { setQuery(e.target.value); setActiveId(null); }}
                onKeyDown={onKeyDown}
                placeholder={t('chat.agentPicker.search')}
                aria-label={t('chat.agentPicker.search')}
                className="flex-1 min-w-0 text-sm bg-transparent outline-none border-0 p-0 focus:ring-0"
              />
              <span className="text-[11px] text-gray-400 tabular-nums">{flat.length}</span>
            </div>

            <div className="flex items-center gap-1 px-2 pt-2">
              {SCOPES.filter((s) => s === 'all' || scopeCounts[s] > 0).map((s) => (
                <button key={s} type="button" onClick={() => { setScope(s); setActiveId(null); }} className={chip(scope === s)}>
                  {t(`chat.agentPicker.scope.${s}`)} <span className="tabular-nums opacity-60">{scopeCounts[s]}</span>
                </button>
              ))}
            </div>

            <div ref={listRef} role="listbox" className="flex-1 min-h-0 overflow-y-auto py-1">
              {showNone && (
                <button
                  type="button"
                  role="option"
                  aria-selected={!value}
                  data-agent-id=""
                  onMouseEnter={() => setActiveId('')}
                  onClick={() => pick(NONE)}
                  className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-left transition-colors ${active?.none ? 'bg-gray-50' : ''}`}
                >
                  <span className="w-7 h-7 shrink-0 rounded-lg flex items-center justify-center bg-gray-100 text-gray-500">
                    <Sparkles className="w-4 h-4" />
                  </span>
                  <span className={`min-w-0 flex-1 text-sm truncate ${!value ? 'font-semibold text-gray-900' : 'text-gray-800'}`}>
                    {noneOption.label}
                  </span>
                  {!value && <Check className="w-4 h-4 text-indigo-600 shrink-0" />}
                </button>
              )}
              {flat.length === 0 && (
                <p className="px-3 py-8 text-center text-xs text-gray-400">
                  {agents.length ? t('chat.agentPicker.noMatch') : t('chat.agentPicker.none')}
                </p>
              )}
              {sections.map((section) => (
                <div key={section.key}>
                  <div className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                    {section.key === 'recent' ? text.recent : t(`chat.agentPicker.section.${section.key}`)}
                  </div>
                  {section.items.map((agent) => {
                    const isSelected = agent.id === value;
                    const isActive = agent.id === active?.id;
                    return (
                      <button
                        key={agent.id}
                        type="button"
                        role="option"
                        aria-selected={isSelected}
                        data-agent-id={agent.id}
                        onMouseEnter={() => setActiveId(agent.id)}
                        onClick={() => pick(agent)}
                        className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-left transition-colors ${isActive ? 'bg-gray-50' : ''}`}
                      >
                        <Avatar agent={agent} />
                        <span className="min-w-0 flex-1">
                          <span className={`flex items-center gap-1 text-sm truncate ${isSelected ? 'font-semibold text-gray-900' : 'text-gray-800'}`}>
                            <span className="truncate">{agent.name || agent.id}</span>
                            {agent.is_default_chat_agent && <Star className="w-3 h-3 text-amber-500 shrink-0" />}
                            {agent.has_running_node && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />}
                          </span>
                          <span className="block text-[11px] text-gray-400 truncate">
                            {t('chat.agentPicker.toolsCount', { count: (agent.tools || []).length })}
                          </span>
                        </span>
                        {isSelected && <Check className="w-4 h-4 text-indigo-600 shrink-0" />}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>

          {details && <div className="hidden sm:block flex-1 min-w-0">
            {active?.none ? (
              <NoneDetails
                option={noneOption}
                selected={!value}
                onPick={() => pick(NONE)}
                pickLabel={text.pick}
                currentLabel={text.current}
              />
            ) : active ? (
              <AgentDetails
                agent={active}
                count={countsIn?.get(active.id) || 0}
                countLabel={text.count}
                pickLabel={text.pick}
                currentLabel={text.current}
                selected={active.id === value}
                onPick={() => pick(active)}
                t={t}
              />
            ) : (
              <div className="h-full flex items-center justify-center text-xs text-gray-400 px-6 text-center">
                {t('chat.agentPicker.hoverHint')}
              </div>
            )}
          </div>}
        </div>
      )}
    </div>
  );
}
