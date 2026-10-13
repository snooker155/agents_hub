import { useEffect, useState } from 'react';
import {
  Siren, Users, Github, Database, Receipt, Scale,
  Headset, Telescope, NotebookPen, ClipboardCheck, PenLine, Target, LayoutTemplate, Check, CheckCircle2, XCircle, RefreshCw,
} from 'lucide-react';
import { getAgentTemplates, getAgentTemplate } from '../../api';
import { useI18n } from '../../i18n';
import { templateText } from './agentTemplates';

// template.icon is a plain word from agents/templates/<id>.md; an unknown one
// falls back to a generic glyph rather than guessing at an icon name.
const ICONS = {
  siren: Siren, users: Users, github: Github, database: Database, receipt: Receipt, scale: Scale,
  headset: Headset, telescope: Telescope, notebook: NotebookPen, clipboard: ClipboardCheck, pen: PenLine, target: Target,
};

export function ConnectorChip({ c }) {
  const { t } = useI18n();
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border ${
        c.configured ? 'bg-green-50 text-green-700 border-green-200' : 'bg-amber-50 text-amber-700 border-amber-200'
      }`}
      title={c.configured ? t('agentTemplates.connected') : t('agentTemplates.notConnected')}
    >
      {c.configured ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
      {c.name}
    </span>
  );
}

/**
 * The create wizard's template step: a grid of ready agents. Picking one
 * loads it in full (prompt included) and hands it to `onPick`, which fills
 * the wizard's fields from it.
 */
export default function AgentTemplatePicker({ workspace, selectedId, onPick }) {
  const { t } = useI18n();
  const [templates, setTemplates] = useState(null);
  const [error, setError] = useState('');
  const [loadingId, setLoadingId] = useState('');

  useEffect(() => {
    let alive = true;
    getAgentTemplates(workspace || undefined)
      .then((r) => { if (alive) setTemplates(r.data || []); })
      .catch(() => { if (alive) { setTemplates([]); setError(t('agentTemplates.loadFailed')); } });
    return () => { alive = false; };
  }, [workspace, t]);

  const pick = async (summary) => {
    setLoadingId(summary.id);
    setError('');
    try {
      const { data } = await getAgentTemplate(summary.id, workspace || undefined);
      onPick(data);
    } catch {
      setError(t('agentTemplates.loadFailed'));
    } finally {
      setLoadingId('');
    }
  };

  if (templates === null) {
    return <p className="text-sm text-gray-400">{t('agentTemplates.loading')}</p>;
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-gray-500">{t('agentTemplates.pickHint')}</p>
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {templates.map((tpl) => {
          const Icon = ICONS[tpl.icon] || LayoutTemplate;
          const { name, description } = templateText(t, tpl);
          const selected = selectedId === tpl.id;
          const connectors = [...(tpl.connectors?.required || []), ...(tpl.connectors?.optional || [])];
          return (
            <button
              key={tpl.id}
              type="button"
              onClick={() => pick(tpl)}
              disabled={!!loadingId}
              className={`text-left p-3 rounded-xl border-2 transition-all flex flex-col gap-2 ${
                selected ? 'border-indigo-500 bg-indigo-50' : 'border-gray-200 hover:border-gray-300 bg-white'
              }`}
            >
              <div className="flex items-start gap-2">
                <div className={`p-1.5 rounded ${selected ? 'bg-indigo-100 text-indigo-700' : 'bg-indigo-50 text-indigo-600'}`}>
                  {loadingId === tpl.id ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Icon className="w-4 h-4" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className={`text-sm font-semibold truncate ${selected ? 'text-indigo-700' : 'text-gray-800'}`}>{name}</div>
                  <div className="text-[11px] text-gray-400 truncate">
                    {t(`agentTemplates.categories.${tpl.category}`, { defaultValue: tpl.category })}
                  </div>
                </div>
                {selected && <Check className="w-4 h-4 text-indigo-600 flex-shrink-0" />}
              </div>
              <p className="text-xs text-gray-500 leading-relaxed line-clamp-3">{description}</p>
              {connectors.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {connectors.map((c) => <ConnectorChip key={c.name} c={c} />)}
                </div>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
