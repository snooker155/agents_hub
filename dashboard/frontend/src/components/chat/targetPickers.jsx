/**
 * The three pickers above the composer: an agent, a team or a flow.
 */
import { useI18n } from '../../i18n';
import { Bot, ChevronDown, UsersRound, Workflow } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

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

export { AgentDropdown, TeamDropdown, FlowDropdown };
