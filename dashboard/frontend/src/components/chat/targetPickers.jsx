/**
 * The pickers in the top bar: an agent, a team, a flow, and the project the
 * conversation works in. One dropdown drawn four ways, so they line up and
 * behave alike: the same height as the mode toggle beside them, an icon in
 * the accent of what they pick, the name, and a list that closes on a click
 * outside or a pick.
 */
import { useI18n } from '../../i18n';
import { Bot, ChevronDown, FolderGit2, UsersRound, Workflow } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

const ACCENTS = {
  indigo: { icon: 'text-indigo-500', hover: 'hover:bg-indigo-50', active: 'bg-indigo-50 text-indigo-700 font-medium' },
  emerald: { icon: 'text-emerald-500', hover: 'hover:bg-emerald-50', active: 'bg-emerald-50 text-emerald-700 font-medium' },
  amber: { icon: 'text-amber-500', hover: 'hover:bg-amber-50', active: 'bg-amber-50 text-amber-700 font-medium' },
};

/**
 * @param {object} props
 * @param {React.ComponentType} props.icon   what is being picked.
 * @param {'indigo'|'emerald'|'amber'} props.accent
 * @param {{id: string, name: string, subtitle?: string}[]} props.items
 * @param {string} props.value               the picked id ('' for none).
 * @param {function} props.onChange
 * @param {string} props.placeholder         the button's text with nothing picked.
 * @param {string} [props.emptyText]         the list's text with nothing to pick.
 * @param {string} [props.title]             the button's tooltip.
 * @param {string} [props.width]             the list's minimum width class.
 */
export function PickerDropdown({
  icon: Icon, accent = 'indigo', items, value, onChange, placeholder, emptyText = '', title = '',
  width = 'min-w-[220px]',
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const selected = items.find((i) => i.id === value);
  const colors = ACCENTS[accent] || ACCENTS.indigo;

  useEffect(() => {
    if (!open) return undefined;
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', handler);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', handler);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        title={title || undefined}
        className="h-8 flex items-center gap-1.5 px-2.5 rounded-lg border border-gray-200 bg-white text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors max-w-[240px]"
      >
        <Icon className={`w-4 h-4 shrink-0 ${colors.icon}`} />
        <span className={`truncate ${selected ? '' : 'text-gray-500'}`}>{selected?.name || placeholder}</span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" />
      </button>

      {open && (
        <div className={`absolute top-full left-0 mt-1 bg-white border border-gray-200 rounded-xl shadow-lg z-50 ${width} py-1 max-h-64 overflow-y-auto`}>
          {items.length === 0 && emptyText && (
            <div className="px-4 py-2 text-xs text-gray-400">{emptyText}</div>
          )}
          {items.map((item) => (
            <button
              key={item.id || '__none'}
              type="button"
              onClick={() => { onChange(item.id); setOpen(false); }}
              className={`w-full text-left px-4 py-2.5 text-sm transition-colors ${colors.hover}
                ${item.id === value ? colors.active : 'text-gray-700'}`}
            >
              <div className={`truncate ${item.id ? 'font-medium' : 'text-gray-500'}`}>{item.name}</div>
              {item.subtitle && <div className="text-xs text-gray-400 mt-0.5">{item.subtitle}</div>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function AgentDropdown({ agents, value, onChange }) {
  const { t } = useI18n();
  return (
    <PickerDropdown
      icon={Bot} accent="indigo" value={value} onChange={onChange}
      placeholder={t('chat.selectAgent')}
      items={agents.map((a) => ({ id: a.id, name: a.name }))}
    />
  );
}

function TeamDropdown({ teams, value, onChange }) {
  const { t } = useI18n();
  return (
    <PickerDropdown
      icon={UsersRound} accent="amber" value={value} onChange={onChange}
      placeholder={t('chat.selectTeam')} emptyText={t('chat.noTeamsDefined')} width="min-w-[280px]"
      items={teams.map((team) => ({
        id: team.team_id,
        name: team.name,
        subtitle: `${team.mode} · ${(team.members || []).length} member${(team.members || []).length === 1 ? '' : 's'}`,
      }))}
    />
  );
}

function FlowDropdown({ flows, value, onChange }) {
  const { t } = useI18n();
  return (
    <PickerDropdown
      icon={Workflow} accent="emerald" value={value} onChange={onChange}
      placeholder={t('chat.selectFlow')} emptyText={t('chat.noFlowsDefined')} width="min-w-[260px]"
      items={flows.map((f) => ({
        id: f.id, name: f.name, subtitle: t('chat.nodeCount', { count: (f.nodes || []).length }),
      }))}
    />
  );
}

/** The project the conversation works in, or none. */
function ProjectDropdown({ projects, value, onChange }) {
  const { t } = useI18n();
  return (
    <PickerDropdown
      icon={FolderGit2} accent="emerald" value={value} onChange={onChange}
      placeholder={t('chat.noProject')} title={t('chat.projectPicker')}
      items={[{ id: '', name: t('chat.noProject') }, ...projects.map((p) => ({ id: p.id, name: p.name }))]}
    />
  );
}

export { AgentDropdown, TeamDropdown, FlowDropdown, ProjectDropdown };
