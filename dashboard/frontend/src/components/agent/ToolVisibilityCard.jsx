import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Eye, Loader } from 'lucide-react';
import { getAgentToolVisibility } from '../../api/agents';
import { updateAgentLoopSettings } from '../../api/agentLoop';
import { toolLabel } from '../../lib/toolText';

const triState = (value) => (value === 'on' || value === 'off' ? value : '');
const fromTriState = (value) => (value === '' ? null : value === 'on');

/**
 * The top of the Tools tab: how many tools the agent's model is shown in a
 * chat, the threshold above which the rest go behind `search_tools`, the
 * tools behind it, and the agent's tool search switch (Automatic, On, Off).
 * Counted on the backend from the agent as a chat builds it
 * (agents/loop_ext/tool_search.py visibility), so the numbers are the real
 * ones. `refreshKey` changes when the saved tool list does.
 */
export default function ToolVisibilityCard({ agentId, workspace, refreshKey, t }) {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  // The breakdown row whose tools are listed, if any.
  const [open, setOpen] = useState('');
  // Bumped after the switch is saved, to count again.
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getAgentToolVisibility(agentId, workspace || undefined)
      .then(({ data: body }) => { if (!cancelled) { setData(body); setFailed(false); } })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [agentId, workspace, refreshKey, reload]);

  const changeMode = async (value) => {
    setSaving(true);
    try {
      await updateAgentLoopSettings(agentId, { tool_search: fromTriState(value) });
      setReload((n) => n + 1);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  const hidden = data?.hidden || [];
  // Why each shown tool is shown, then what is left for search. Each row
  // opens to name its tools; the first one also says why it is there.
  const rows = data?.search ? [
    { key: 'breakdownAlways', tools: data.always || [], why: 'alwaysWhy' },
    { key: 'breakdownRuntime', tools: data.runtime },
    { key: 'breakdownNamed', tools: data.named || [] },
    { key: 'breakdownHidden', tools: hidden, strong: true },
  ].filter((row) => row.tools.length > 0) : [];

  return (
    <div className="bg-white p-6 shadow-md rounded-lg" data-testid="tool-visibility">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h3 className="text-lg font-bold flex items-center">
            <Eye className="w-5 h-5 mr-2 text-indigo-600" />
            {t('agentLoop.toolSearch.summaryTitle')}
          </h3>
          {!data && !failed && <Loader className="w-4 h-4 animate-spin text-gray-400" />}
          {failed && <p className="text-xs text-red-600">{t('agentLoop.toolSearch.loadFailed')}</p>}
          {data && (
            <>
              <p className="text-xs text-gray-600">
                {t('agentLoop.toolSearch.composition', {
                  total: data.total, picked: data.total - data.runtime.length, runtime: data.runtime.length,
                })}
              </p>
              <p className="text-sm font-semibold text-gray-800">
                {t('agentLoop.toolSearch.visible', { visible: data.visible.length, total: data.total })}
              </p>
              {data.search ? (
                <ul className="text-xs text-gray-700 space-y-1">
                  {rows.map((row) => (
                    <li key={row.key}>
                      <button
                        type="button"
                        onClick={() => setOpen((v) => (v === row.key ? '' : row.key))}
                        className={`flex items-center gap-1 text-left hover:text-indigo-700 ${row.strong ? 'font-semibold' : ''}`}
                        aria-expanded={open === row.key}
                      >
                        {open === row.key ? <ChevronDown className="w-3.5 h-3.5 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 shrink-0" />}
                        {t(`agentLoop.toolSearch.${row.key}`, { n: row.tools.length, limit: data.named_limit })}
                      </button>
                      {open === row.key && (
                        <div className="mt-1.5 ml-5 space-y-1.5">
                          {row.why && <p className="text-gray-600 max-w-2xl">{t(`agentLoop.toolSearch.${row.why}`)}</p>}
                          <div className="flex flex-wrap gap-1.5">
                            {row.tools.map((name) => (
                              <span key={name} title={name}
                                    className="text-[11px] px-2 py-0.5 rounded-full border border-gray-300 bg-white text-gray-800">
                                {toolLabel(t, name)}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-gray-600">
                  {t(data.mode === 'off' ? 'agentLoop.toolSearch.searchOff' : 'agentLoop.toolSearch.notNeeded')}
                </p>
              )}
              <p className="text-xs text-gray-500">{t('agentLoop.toolSearch.threshold', { threshold: data.threshold })}</p>
            </>
          )}
        </div>
        <label className="flex flex-col gap-1 text-xs text-gray-700">
          <span className="font-medium">{t('agentLoop.toolSearch.title')}</span>
          <select
            value={triState(data?.mode)}
            disabled={!data || saving}
            onChange={(e) => changeMode(e.target.value)}
            aria-label={t('agentLoop.toolSearch.title')}
            className="border border-gray-200 rounded-lg px-2 py-1 text-xs bg-white focus:outline-none"
          >
            <option value="">{t('agentLoop.triState.automatic')}</option>
            <option value="on">{t('agentLoop.triState.on')}</option>
            <option value="off">{t('agentLoop.triState.off')}</option>
          </select>
          <span className="text-[11px] text-gray-500 max-w-xs">{t('agentLoop.toolSearch.hint')}</span>
        </label>
      </div>
    </div>
  );
}
