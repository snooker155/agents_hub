/**
 * A delegated run as the Chat view shows it while the turn is live: who is
 * working, and the one step it is on. Each step takes the place of the one
 * before, the way the parent's own steps do, so the worker's tool calls never
 * pile up in the reply. Once the worker is done the line says so, with the
 * start of its answer, until the parent moves on. After the turn nothing of it
 * stays in the reply: the whole trail is in the Build view and the Process panel.
 */
import { BrainCircuit, CheckCircle2, MessageSquareText, Repeat, Terminal, Workflow, XCircle, Zap } from 'lucide-react';
import { useI18n } from '../../i18n';
import { SKILL_TOOL, shortText } from '../processUtils';
import { foldDelegationTools } from './trail';
import ToolStatusMark from '../ToolStatusMark';

function Dots({ tone = 'bg-indigo-400' }) {
  return (
    <span className="flex gap-1 flex-shrink-0">
      {[0, 150, 300].map((d) => (
        <span key={d} className={`w-1 h-1 ${tone} rounded-full animate-bounce`} style={{ animationDelay: `${d}ms` }} />
      ))}
    </span>
  );
}

// The last three lines of text still being written.
function Tail({ icon: Icon, iconCls, text }) {
  return (
    <div className="flex items-start gap-1.5">
      <Icon className={`w-3.5 h-3.5 flex-shrink-0 mt-0.5 ${iconCls}`} />
      <div className="flex-1 min-w-0 max-h-[3.75rem] overflow-hidden flex flex-col justify-end">
        <div className="text-[11px] leading-5 text-gray-500 whitespace-pre-wrap break-words">{text}</div>
      </div>
    </div>
  );
}

// The step the worker is on: the last one of its trail that says anything.
function currentStep(entry) {
  const list = foldDelegationTools(entry.timeline);
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const e = list[i];
    if (e?.type === 'text' && !(e.text || '').trim()) continue;
    if (e?.type === 'reasoning' && !(e.content || '').trim()) continue;
    return { step: e, index: i };
  }
  return { step: null, index: -1 };
}

function Step({ step }) {
  const { t } = useI18n();
  if (!step) {
    return <span className="flex items-center gap-2 text-[11px] text-gray-400">{t('chat.working')} <Dots tone="bg-gray-400" /></span>;
  }
  if (step.type === 'delegation') return <LiveDelegation entry={step} />;
  if (step.type === 'reasoning') return <Tail icon={BrainCircuit} iconCls="text-violet-500 animate-pulse" text={step.content} />;
  if (step.type === 'text') return <Tail icon={MessageSquareText} iconCls="text-gray-400" text={step.text} />;
  if (step.type === 'graph_node') {
    return (
      <span className="flex items-center gap-1.5 text-[11px] text-gray-600">
        <Workflow className="w-3.5 h-3.5 text-indigo-500 flex-shrink-0" />
        <span className="font-medium truncate">{step.node}</span>
        {step.running && <Dots />}
      </span>
    );
  }
  if (step.type === 'tool') {
    const isSkill = step.tool === SKILL_TOOL;
    return (
      <span className="flex items-center gap-1.5 min-w-0 text-[11px]" data-testid="live-delegation-tool">
        <ToolStatusMark entry={step} />
        {isSkill
          ? <Zap className="w-3.5 h-3.5 text-violet-500 flex-shrink-0" />
          : <Terminal className="w-3.5 h-3.5 text-amber-600 flex-shrink-0" />}
        <span className={`font-semibold flex-shrink-0 ${isSkill ? 'text-violet-700' : 'text-amber-700'}`}>{step.tool || 'tool'}</span>
        {step.input ? <span className="text-gray-400 truncate">{shortText(step.input, 80)}</span> : null}
      </span>
    );
  }
  return null;
}

export default function LiveDelegation({ entry }) {
  const { t } = useI18n();
  const name = entry.agent_name || entry.agent_id;
  if (!entry.running) {
    const failed = entry.ok === false;
    const answer = (failed ? entry.error : entry.output) || '';
    return (
      <div className="chat-step-enter rounded-lg border border-indigo-100 bg-indigo-50/40 px-3 py-2" data-testid="live-delegation-done">
        <div className="flex items-center gap-1.5 text-xs font-semibold">
          {failed
            ? <XCircle className="w-3.5 h-3.5 text-red-500 flex-shrink-0" />
            : <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" />}
          <span className={failed ? 'text-red-700' : 'text-indigo-700'}>
            {t(failed ? 'chat.delegateFailed' : 'chat.delegateFinished', { agent: name })}
          </span>
        </div>
        {answer.trim() && (
          <div className="mt-1 text-[11px] leading-5 text-gray-500 whitespace-pre-wrap break-words line-clamp-3">
            {answer.trim()}
          </div>
        )}
      </div>
    );
  }
  const { step, index } = currentStep(entry);
  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/40 px-3 py-2" data-testid="live-delegation">
      <div className="flex items-center gap-1.5">
        <Repeat className="w-3.5 h-3.5 text-indigo-500 flex-shrink-0" />
        <span className="text-xs font-semibold text-indigo-700 truncate">{t('chat.delegatedTo', { agent: name })}</span>
        <Dots />
      </div>
      {/* Keyed by position: a new step replaces the last one and fades in,
          while the step in place (a tool finishing) just updates. */}
      <div key={index} className="chat-step-enter mt-1.5 min-w-0">
        <Step step={step} />
      </div>
    </div>
  );
}
