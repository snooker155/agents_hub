import { useEffect, useRef, useState } from 'react';
import { Play, Square, Sparkles } from 'lucide-react';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import LiveMark from '../components/liveMark/LiveMark';
import useSteadyState from '../components/liveMark/useSteadyState';
import { STATES } from '../components/liveMark/poses';
import { REBUILD_STATES, RELAY_STATES, SEARCH_STATES, WORK_STATES } from '../components/liveMark/scenes';
import { stateForTool, stateForTurn } from '../components/liveMark/activity';

/*
 * A bench for the live mark (components/liveMark): every state on a button,
 * the poses and the artifact's scenes, relay stories and rebuilds of the
 * mark in their groups (a clip's button carries the artifact's name for
 * it), a scripted run with
 * the timings a real one has (including a lookup that returns in 140 ms, to
 * show what the hold does), the mark at the sizes it is
 * used at, and a box that tells which state a tool name maps to. Not in the
 * navigation; reached at /mark-lab.
 */

const RUN = [
  { thinking: true, ms: 1500 },
  { tool: 'web_search', ms: 4000 },
  { tool: 'fetch_url', ms: 3500 },
  { tool: 'search_memory', ms: 3000 },
  { tool: 'get_task', ms: 140 },
  { tool: 'think', ms: 3500 },
  { tool: 'schedule_task', ms: 3000 },
  { tool: 'write_file', ms: 5000 },
  { tool: 'run_code', ms: 2000 },
  { tool: 'create_view', ms: 4500 },
  { tool: 'ask_special_model', ms: 3000 },
  { tool: 'notify_user', ms: 3500 },
  { waiting: true, ms: 2000 },
  { text: true, ms: 2500 },
]

const GROUPS = [['poses', STATES], ['search', SEARCH_STATES], ['work', WORK_STATES], ['relay', RELAY_STATES], ['rebuild', REBUILD_STATES]];

function stepLabel(step, t) {
  if (step.tool) return t('liveMark.lab.step', { tool: step.tool, ms: step.ms });
  return t('liveMark.lab.step', { tool: t(`liveMark.states.${stateForTurn(step)}`), ms: step.ms });
}

export default function MarkLab() {
  const { t } = useI18n();
  const [requested, setRequested] = useState('idle');
  const [hold, setHold] = useState(true);
  const [stepIdx, setStepIdx] = useState(-1);
  const [tool, setTool] = useState('');
  const timer = useRef(0);
  const shown = useSteadyState(requested, hold ? 700 : 0);

  useEffect(() => () => clearTimeout(timer.current), []);

  const stop = () => {
    clearTimeout(timer.current);
    setStepIdx(-1);
    setRequested('idle');
  };
  const play = () => {
    clearTimeout(timer.current);
    const run = (i) => {
      if (i >= RUN.length) { stop(); return; }
      setStepIdx(i);
      setRequested(stateForTurn(RUN[i]));
      timer.current = setTimeout(() => run(i + 1), RUN[i].ms);
    };
    run(0);
  };
  const pick = (state) => {
    clearTimeout(timer.current);
    setStepIdx(-1);
    setRequested(state);
  };

  return (
    <PageContainer className="space-y-6">
      <PageHeader icon={Sparkles} title={t('liveMark.lab.title')} description={t('liveMark.lab.subtitle')} />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <section className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col items-center gap-5">
          <div className="py-6">
            <LiveMark state={shown} minHold={0} size={200} />
          </div>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
            <dt className="text-gray-500">{t('liveMark.lab.requested')}</dt>
            <dd className="font-medium text-gray-800">{t(`liveMark.states.${requested}`)}</dd>
            <dt className="text-gray-500">{t('liveMark.lab.shown')}</dt>
            <dd className="font-medium text-gray-800" data-testid="mark-shown">{t(`liveMark.states.${shown}`)}</dd>
          </dl>
          <div className="w-full">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-3">{t('liveMark.lab.sizes')}</h3>
            <div className="flex items-end justify-center gap-8">
              <LiveMark state={shown} minHold={0} size={24} label="" />
              <LiveMark state={shown} minHold={0} size={36} label="" />
              <LiveMark state={shown} minHold={0} size={64} label="" />
              <LiveMark state={shown} minHold={0} size={32} frame="logo" label="" />
            </div>
          </div>
        </section>

        <section className="space-y-5">
          <div className="bg-white border border-gray-200 rounded-xl p-5">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-3">{t('liveMark.lab.states')}</h3>
            <div className="space-y-4">
              {GROUPS.map(([group, states]) => (
                <div key={group}>
                  <p className="text-xs text-gray-500 mb-2">{t(`liveMark.lab.groups.${group}`)}</p>
                  <div className="flex flex-wrap gap-2">
                    {states.map((s) => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => pick(s)}
                        title={t(`liveMark.states.${s}`)}
                        className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${requested === s && stepIdx < 0
                          ? 'bg-indigo-600 border-indigo-600 text-white'
                          : 'bg-white border-gray-200 text-gray-700 hover:border-indigo-400'}`}
                      >
                        {t(`liveMark.lab.clips.${s}`, { defaultValue: t(`liveMark.states.${s}`) })}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <p className="text-xs text-gray-500 mt-4">{t('liveMark.lab.scenesHint')}</p>
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-4">
            <div className="flex items-center gap-3">
              {stepIdx < 0 ? (
                <button type="button" onClick={play}
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm bg-indigo-600 text-white hover:bg-indigo-500">
                  <Play className="w-4 h-4" /> {t('liveMark.lab.playRun')}
                </button>
              ) : (
                <button type="button" onClick={stop}
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm border border-gray-200 text-gray-700 hover:border-indigo-400">
                  <Square className="w-4 h-4" /> {t('liveMark.lab.stopRun')}
                </button>
              )}
              <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                <input type="checkbox" checked={hold} onChange={(e) => setHold(e.target.checked)} className="accent-indigo-600" />
                {t('liveMark.lab.hold')}
              </label>
            </div>
            <p className="text-xs text-gray-500">{t('liveMark.lab.holdHint')}</p>
            <ol className="text-sm space-y-1">
              {RUN.map((step, i) => (
                <li key={i} className={`flex items-center gap-2 ${i === stepIdx ? 'text-indigo-700 font-medium' : 'text-gray-500'}`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${i === stepIdx ? 'bg-indigo-600' : 'bg-gray-300'}`} />
                  <span className="font-mono text-xs">{stepLabel(step, t)}</span>
                  <span className="text-xs text-gray-400">→ {t(`liveMark.states.${stateForTurn(step)}`)}</span>
                </li>
              ))}
            </ol>
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-2">
            <label htmlFor="mark-tool" className="text-xs font-semibold uppercase tracking-wide text-gray-500">{t('liveMark.lab.toolLabel')}</label>
            <div className="flex items-center gap-3">
              <input
                id="mark-tool"
                value={tool}
                onChange={(e) => setTool(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && tool.trim()) pick(stateForTool(tool.trim())); }}
                placeholder={t('liveMark.lab.toolPlaceholder')}
                className="flex-1 min-w-0 px-3 py-1.5 text-sm font-mono border border-gray-200 rounded-lg focus:outline-none"
              />
              {tool.trim() ? (
                <button type="button" onClick={() => pick(stateForTool(tool.trim()))}
                  className="px-3 py-1.5 rounded-lg text-sm border border-gray-200 text-gray-700 hover:border-indigo-400">
                  {t(`liveMark.states.${stateForTool(tool.trim())}`)}
                </button>
              ) : null}
            </div>
            <p className="text-xs text-gray-500">{t('liveMark.lab.toolHint')}</p>
          </div>
        </section>
      </div>
    </PageContainer>
  );
}
