import React from 'react';
import { Wrench } from 'lucide-react';
import { useI18n } from '../../i18n';
import { loadedToolsOf } from './loadedToolsOf';

/**
 * The tools a run was given beyond the ones it saw from the start
 * (`run.loop.loaded_tools`, agents/agent_loop.py): found with search_tools
 * (agents/loop_ext/tool_search.py) or shown when a view of their kind became
 * the run's target (agents/loop_ext/view_focus.py). Loaded, not necessarily
 * called: the calls are in the process steps.
 */
export default function LoadedTools({ run }) {
  const { t } = useI18n();
  const tools = loadedToolsOf(run);
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-3" data-testid="loaded-tools">
      <div className="flex items-center gap-2">
        <Wrench className="w-4 h-4 text-gray-400" />
        <h4 className="text-xs font-semibold text-gray-600 uppercase tracking-wide">
          {t('runLoop.loadedTools')} ({tools.length})
        </h4>
      </div>
      <p className="text-sm text-gray-500">{t('runLoop.loadedToolsHint')}</p>
      <div className="flex flex-wrap gap-1.5">
        {tools.map((name) => (
          <span key={name} className="text-xs font-mono px-2 py-0.5 rounded bg-gray-100 text-gray-700">
            {name}
          </span>
        ))}
      </div>
    </div>
  );
}
