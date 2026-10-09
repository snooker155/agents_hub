/**
 * Task header widgets: status and priority badges and dropdowns, workspace badge, project selector, due date.
 */
import { Calendar, Check, ChevronDown, Flag, Folder } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import DateInput from '../DateInput';
import { ALL_STATUSES, PRIORITIES, priorityCfg, statusCfg } from './taskUtils';
import { useI18n } from '../../i18n';

export function StatusBadge({ status, size = 'sm' }) {
  const { t } = useI18n();
  const s = statusCfg(status);
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-medium ${
      size === 'xs' ? 'text-xs' : 'text-sm'
    } ${s.bg} ${s.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {t(`taskStatus.${s.value}`)}
    </span>
  );
}

export function PriorityBadge({ priority }) {
  const { t } = useI18n();
  if (!priority) return null;
  const p = priorityCfg(priority);
  if (!p) return null;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs font-medium ${p.color} ${p.bg} ${p.border}`}>
      <Flag className="w-3 h-3" />
      {t(`priority.${p.value}`)}
    </span>
  );
}

// Status dropdown with click-outside close
export function StatusDropdown({ current, onChange }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const s = statusCfg(current);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border ${s.bg} ${s.text} border-transparent hover:border-current transition-colors`}
      >
        <span className={`w-2 h-2 rounded-full ${s.dot}`} />
        {t(`taskStatus.${s.value}`)}
        <ChevronDown className="w-3.5 h-3.5 ml-0.5" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-44 bg-white border border-gray-200 rounded-lg shadow-lg z-20 py-1">
          {ALL_STATUSES.filter(opt => !opt.readonly).map(opt => (
            <button
              key={opt.value}
              onClick={() => { onChange(opt.value); setOpen(false); }}
              className={`w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50 ${opt.value === current ? 'font-semibold' : ''}`}
            >
              <span className={`w-2 h-2 rounded-full ${opt.dot}`} />
              {t(`taskStatus.${opt.value}`)}
              {opt.value === current && <Check className="w-3.5 h-3.5 ml-auto text-indigo-600" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Priority dropdown
export function PriorityDropdown({ current, onChange }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const p = priorityCfg(current);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border ${
          p ? `${p.color} ${p.bg} ${p.border}` : 'text-gray-500 bg-gray-50 border-gray-200'
        } hover:opacity-80 transition-opacity`}
      >
        <Flag className="w-3 h-3" />
        {p ? t(`priority.${p.value}`) : t('taskDetails.noPriority')}
        <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-36 bg-white border border-gray-200 rounded-lg shadow-lg z-20 py-1">
          <button
            onClick={() => { onChange(null); setOpen(false); }}
            className="w-full flex items-center gap-2 px-3 py-2 text-xs text-gray-500 hover:bg-gray-50"
          >
            <Flag className="w-3 h-3" />
            {t('taskDetails.noPriority')}
            {!current && <Check className="w-3 h-3 ml-auto text-indigo-600" />}
          </button>
          {PRIORITIES.map(opt => (
            <button
              key={opt.value}
              onClick={() => { onChange(opt.value); setOpen(false); }}
              className={`w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-gray-50 ${opt.color}`}
            >
              <Flag className="w-3 h-3" />
              {t(`priority.${opt.value}`)}
              {opt.value === current && <Check className="w-3 h-3 ml-auto text-indigo-600" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Workspace link badge
export function WorkspaceBadge({ current }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  if (!current) return null;
  return (
    <button
      onClick={() => navigate(`/workspaces/${encodeURIComponent(current)}`)}
      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 border border-indigo-200 bg-indigo-50 rounded-lg text-xs font-medium text-indigo-700 hover:bg-indigo-100 transition-colors"
      title={t('taskDetails.openWorkspace')}
    >
      <Folder className="w-3 h-3" />
      {current}
    </button>
  );
}

// Project selector dropdown
export function ProjectSelector({ current, projects, onChange }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const navigate = useNavigate();
  const currentProject = projects.find(p => p.id === current);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div ref={ref} className="relative inline-flex items-center border border-indigo-200 bg-indigo-50 rounded-lg overflow-visible text-xs font-medium text-indigo-700">
      {currentProject ? (
        <button
          onClick={() => navigate(`/projects/${currentProject.id}`)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 hover:bg-indigo-100 transition-colors"
          title={t('taskDetails.openProject')}
        >
          <Folder className="w-3 h-3" />
          {currentProject.name}
        </button>
      ) : (
        <span className="flex items-center gap-1.5 px-2.5 py-1.5 text-indigo-400">
          <Folder className="w-3 h-3" />
          {t('taskDetails.noProject')}
        </span>
      )}
      <button
        onClick={() => setOpen(o => !o)}
        className="px-1.5 py-1.5 border-l border-indigo-200 hover:bg-indigo-100 transition-colors"
        title={t('taskDetails.changeProject')}
      >
        <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-44 bg-white border border-gray-200 rounded-lg shadow-lg z-20 py-1 max-h-52 overflow-auto">
          <button
            onClick={() => { onChange(null); setOpen(false); }}
            className="w-full flex items-center gap-2 px-3 py-2 text-xs text-gray-500 hover:bg-gray-50"
          >
            No project
            {!current && <Check className="w-3 h-3 ml-auto text-indigo-600" />}
          </button>
          {projects.map(p => (
            <button
              key={p.id}
              onClick={() => { onChange(p.id); setOpen(false); }}
              className="w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-gray-50 text-gray-700"
            >
              <span className="truncate">{p.name}</span>
              {p.id === current && <Check className="w-3 h-3 ml-auto text-indigo-600 flex-shrink-0" />}
            </button>
          ))}
          {projects.length === 0 && (
            <p className="px-3 py-2 text-xs text-gray-400 italic">{t('taskDetails.noProjectsFound')}</p>
          )}
        </div>
      )}
    </div>
  );
}

// Due date field: a badge that opens a date picker; turns red once overdue.
// `overdue` comes from the task record (the backend already derives it from
// due_at + status), so this never computes against the current time itself.
export function DueDateField({ current, overdue, onChange }) {
  const { t, language } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const label = current
    ? new Intl.DateTimeFormat(language, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(current))
    : t('taskDetails.noDueDate');

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-opacity hover:opacity-80 ${
          overdue ? 'text-red-600 bg-red-50 border-red-200' : current ? 'text-gray-600 bg-gray-50 border-gray-200' : 'text-gray-400 bg-gray-50 border-gray-200'
        }`}
        title={t('taskDetails.dueDate')}
      >
        <Calendar className="w-3 h-3" />
        {label}
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-20 p-2">
          <DateInput
            mode="datetime"
            valueFormat="iso"
            value={current || ''}
            onChange={(v) => onChange(v || null)}
            className="border border-gray-300 rounded-md px-2 py-1 text-sm"
          />
          {current && (
            <button
              onClick={() => { onChange(null); setOpen(false); }}
              className="mt-2 w-full text-xs text-gray-500 hover:text-gray-700 text-center"
            >
              {t('common.clear')}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
