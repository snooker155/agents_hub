import { useEffect, useState } from 'react';
import {
  BadgeCheck, BarChart3, Briefcase, Check, ClipboardList, Code2, FilePlus2, GitPullRequest,
  LayoutTemplate, PenLine, Search, Siren, Store, Target, Users,
} from 'lucide-react';
import { getFlowTemplates } from '../../api/flows';
import { useI18n } from '../../i18n';
import { flowTemplateText } from './flowTemplates';

// template.icon is a plain word from flow/templates/<id>.yaml; an unknown one
// falls back to a generic glyph rather than guessing at an icon name.
const ICONS = {
  search: Search, target: Target, chart: BarChart3, check: BadgeCheck, pen: PenLine,
  code: Code2, git: GitPullRequest, siren: Siren, users: Users, store: Store,
  briefcase: Briefcase, clipboard: ClipboardList,
};

function Card({ selected, onClick, onDoubleClick, icon: Icon, title, subtitle, children, testId }) {
  return (
    <button
      type="button"
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      data-testid={testId}
      className={`flex flex-col gap-2 rounded-xl border-2 p-3 text-left transition-all ${
        selected ? 'border-cyan-500 bg-cyan-50' : 'border-slate-200 bg-white hover:border-slate-300'
      }`}
    >
      <div className="flex items-start gap-2">
        <div className={`rounded p-1.5 ${selected ? 'bg-cyan-100 text-cyan-700' : 'bg-cyan-50 text-cyan-600'}`}>
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className={`truncate text-sm font-semibold ${selected ? 'text-cyan-800' : 'text-slate-800'}`}>{title}</div>
          <div className="truncate text-[11px] text-slate-400">{subtitle}</div>
        </div>
        {selected && <Check className="h-4 w-4 shrink-0 text-cyan-600" />}
      </div>
      {children}
    </button>
  );
}

/**
 * The Create flow dialog's first choice: a blank canvas, or one of the ready
 * flows from flow/templates/ (GET /api/flow-templates), four across. Each
 * card shows the steps in order, so the person sees which agents will run.
 * `selectedId` is null for the blank canvas; `onPick(template | null)`.
 * A double click picks and calls `onChoose` too, to go straight on.
 */
export default function FlowTemplatePicker({ selectedId, onPick, onChoose }) {
  const { t, language } = useI18n();
  const [templates, setTemplates] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    getFlowTemplates(language)
      .then((r) => { if (alive) setTemplates(r.data || []); })
      .catch(() => { if (alive) { setTemplates([]); setFailed(true); } });
    return () => { alive = false; };
  }, [language]);

  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-500">{t('flowTemplates.pickHint')}</p>
      {failed && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{t('flowTemplates.loadFailed')}</div>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card
          selected={!selectedId}
          onClick={() => onPick(null)}
          onDoubleClick={() => { onPick(null); onChoose?.(); }}
          icon={FilePlus2}
          title={t('flowTemplates.blank')}
          subtitle={t('flowTemplates.blankSubtitle')}
          testId="flow-template-blank"
        >
          <p className="line-clamp-3 text-xs leading-relaxed text-slate-500">{t('agentFlows.startWithABlankCanvas')}</p>
        </Card>
        {templates === null ? (
          <p className="self-center text-sm text-slate-400">{t('flowTemplates.loading')}</p>
        ) : templates.map((tpl) => {
          const { name, description } = flowTemplateText(t, tpl);
          return (
            <Card
              key={tpl.id}
              selected={selectedId === tpl.id}
              onClick={() => onPick(tpl)}
              onDoubleClick={() => { onPick(tpl); onChoose?.(); }}
              icon={ICONS[tpl.icon] || LayoutTemplate}
              title={name}
              subtitle={t(`flowTemplates.categories.${tpl.category}`, { defaultValue: tpl.category })}
              testId={`flow-template-${tpl.id}`}
            >
              <p className="line-clamp-3 text-xs leading-relaxed text-slate-500">{description}</p>
              <div className="flex flex-wrap items-center gap-1 text-[10px] text-slate-500">
                {tpl.steps.map((step, i) => (
                  <span key={step.key} className="inline-flex items-center gap-1">
                    {i > 0 && <span className="text-slate-300">›</span>}
                    <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5" title={step.agent}>
                      {step.label}
                    </span>
                  </span>
                ))}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
