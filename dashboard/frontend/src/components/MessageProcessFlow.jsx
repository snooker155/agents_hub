import { SKILL_TOOL, preview } from './processUtils';
import { toolInline } from './toolFormatters';
import ProcessNode from './ProcessNode';
import { useI18n } from '../i18n';

// Message-page process view. Unlike the shared ProcessGraph (Chat/Task), this
// drops the top-level run-summary node and renders the run's process directly:
// a pinned Input at the top, then a flat list of expandable step nodes
// (thoughts + tool calls in execution order) followed by the Output. Each node
// expands to its full, untruncated content.

export default function MessageProcessFlow({ runs = [] }) {
  const { t } = useI18n();
  if (!runs.length) {
    return <p className="p-4 text-xs text-gray-500 italic">{t('messageProcessFlow.noProcessDataYet')}</p>;
  }

  const input = runs.find((r) => r?.input)?.input || '';
  const output = [...runs].reverse().find((r) => r?.output)?.output || '';

  // Thoughts and tool calls share one step counter, so merging by step renders
  // them in true execution order. Entries without a step sink to the end.
  const steps = [];
  runs.forEach((mr, mi) => {
    [
      ...(mr.reasoning || []).map((r, i) => ({ kind: 'thought', step: r.step, item: r, key: `${mi}-r-${i}` })),
      ...(mr.tools || []).map((tc, i) => ({ kind: 'tool', step: tc.step, item: tc, key: `${mi}-t-${i}` })),
    ]
      .sort((a, b) => (a.step ?? Infinity) - (b.step ?? Infinity))
      .forEach((s) => steps.push(s));
  });

  // Number thoughts by thought order only: tools are the result of a thought, so
  // they must not advance the thought counter (which would make it skip 1, 3, 5…).
  let thoughtNo = 0;
  steps.forEach((s) => {
    if (s.kind === 'thought') {
      thoughtNo += 1;
      s.thoughtNo = thoughtNo;
    }
  });

  return (
    <div>
      {/* Pinned input — stays at the top while the process nodes scroll under it. */}
      <div className="sticky top-0 z-10 bg-blue-50 border-b border-blue-200 px-4 py-3">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-blue-700 mb-1">{t('messageProcessFlow.input')}</div>
        <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-words max-h-40 overflow-auto">
          {input || '(empty)'}
        </div>
      </div>

      <div className="p-4 space-y-2">
        {steps.length === 0 ? (
          <p className="text-xs text-gray-500 italic">{t('messageProcessFlow.noProcessStepsRecorded')}</p>
        ) : (
          steps.map((entry) => {
            if (entry.kind === 'thought') {
              const r = entry.item;
              return (
                <ProcessNode
                  key={entry.key}
                  label={`Thought · step ${entry.thoughtNo}`}
                  labelColor="text-violet-700"
                  borderColor="border-violet-200"
                  bgColor="bg-violet-50"
                  hint={preview(r.content)}
                >
                  <div className="text-[11px] text-violet-900 whitespace-pre-wrap break-words">
                    {r.content || '(empty)'}
                  </div>
                </ProcessNode>
              );
            }

            const tc = entry.item;
            if (tc.tool === SKILL_TOOL) {
              return (
                <ProcessNode
                  key={entry.key}
                  label={t('messageProcessFlow.skillRetrieved')}
                  labelColor="text-violet-800"
                  borderColor="border-violet-300"
                  bgColor="bg-violet-50"
                  hint={preview(tc.input || tc.output)}
                >
                  {tc.input && (
                    <div className="text-[11px] text-violet-700 whitespace-pre-wrap break-all">
                      <span className="text-violet-400">{t('messageProcessFlow.id')}</span> {tc.input}
                    </div>
                  )}
                  {tc.output && (
                    <div className="text-[11px] text-violet-900 whitespace-pre-wrap break-all">
                      {tc.output}
                    </div>
                  )}
                </ProcessNode>
              );
            }

            // think/plan tools carry their content as the input and merely echo
            // it back as output, so the "in:" line is redundant noise.
            const isReasoningTool = tc.tool === 'think' || tc.tool === 'plan';
            return (
              <ProcessNode
                key={entry.key}
                label={`Tool: ${tc.tool || 'tool'}`}
                labelColor="text-amber-700"
                borderColor="border-amber-200"
                bgColor="bg-amber-50"
                hint={toolInline(tc.tool, tc.input)}
              >
                {!isReasoningTool && tc.input && (
                  <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-all">
                    <span className="text-gray-500">{t('messageProcessFlow.in')}</span> {tc.input}
                  </div>
                )}
                {(tc.output || (isReasoningTool && tc.input)) && (
                  <div className="text-[11px] text-emerald-700 whitespace-pre-wrap break-all">
                    <span className="text-emerald-600">{t('messageProcessFlow.out')}</span> {tc.output || tc.input}
                  </div>
                )}
              </ProcessNode>
            );
          })
        )}
      </div>

      {/* Output — always expanded, same form as the pinned input. */}
      {output && (
        <div className="border-t border-green-200 bg-green-50 px-4 py-3">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-green-700 mb-1">{t('messageProcessFlow.output')}</div>
          <div className="text-[11px] text-gray-700 whitespace-pre-wrap break-words">
            {output}
          </div>
        </div>
      )}
    </div>
  );
}
