import React from 'react';
import { SlidersHorizontal } from 'lucide-react';
import { useI18n } from '../../i18n';

/**
 * RunOverrides: what this run was asked to build differently from its agent
 * (`run.overrides`, agents/run_overrides.py): model, instructions, tools,
 * skills, MCP servers, tool policy, answer schema. Renders nothing for a run
 * that had none.
 */

const ORDER = ['provider', 'model', 'system', 'system_append', 'tools', 'skills', 'mcp', 'tool_policy', 'output_schema'];

function Value({ name, value, t }) {
  if (name === 'tools' && value && !Array.isArray(value) && typeof value === 'object') {
    const parts = [];
    if (value.add?.length) parts.push(t('runOverrides.toolsAdd', { tools: value.add.join(', ') }));
    if (value.remove?.length) parts.push(t('runOverrides.toolsRemove', { tools: value.remove.join(', ') }));
    return <span className="font-mono text-xs">{parts.join('; ') || t('runOverrides.none')}</span>;
  }
  if (name === 'skills' && typeof value === 'boolean') {
    return <span className="text-xs">{value ? t('runOverrides.skillsOn') : t('runOverrides.skillsOff')}</span>;
  }
  if (Array.isArray(value)) {
    return <span className="font-mono text-xs">{value.length ? value.join(', ') : t('runOverrides.none')}</span>;
  }
  if (value && typeof value === 'object') {
    return (
      <pre className="text-xs bg-gray-50 border border-gray-100 rounded p-2 overflow-auto max-h-48 whitespace-pre-wrap break-words">
        {JSON.stringify(value, null, 2)}
      </pre>
    );
  }
  if (name === 'system' || name === 'system_append') {
    return (
      <pre className="text-xs bg-gray-50 border border-gray-100 rounded p-2 overflow-auto max-h-48 whitespace-pre-wrap break-words">
        {String(value)}
      </pre>
    );
  }
  return <span className="font-mono text-xs">{String(value)}</span>;
}

export default function RunOverrides({ run }) {
  const { t } = useI18n();
  const overrides = run?.overrides && typeof run.overrides === 'object' ? run.overrides : null;
  const keys = overrides ? ORDER.filter((k) => overrides[k] !== undefined && overrides[k] !== null) : [];
  if (!keys.length) return null;

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-6" data-testid="run-overrides">
      <div className="flex items-center gap-2 mb-1">
        <SlidersHorizontal className="w-4 h-4 text-gray-400" />
        <h4 className="text-xs font-semibold text-gray-600 uppercase tracking-wide">{t('runOverrides.title')}</h4>
      </div>
      <p className="text-xs text-gray-400 mb-3">{t('runOverrides.hint')}</p>
      <dl className="space-y-2">
        {keys.map((k) => (
          <div key={k} className="grid grid-cols-1 sm:grid-cols-[10rem_1fr] gap-1 sm:gap-3 items-start">
            <dt className="text-xs text-gray-500">{t(`runOverrides.keys.${k}`)}</dt>
            <dd className="text-gray-800 min-w-0"><Value name={k} value={overrides[k]} t={t} /></dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
