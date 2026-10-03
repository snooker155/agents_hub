import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useI18n } from '../../../i18n';

const MODE_TONE = {
  new: 'bg-green-50 text-green-700 border-green-200',
  replace: 'bg-blue-50 text-blue-700 border-blue-200',
  extend: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  remove: 'bg-red-50 text-red-700 border-red-200',
};

function Collapsible({ title, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-gray-100 rounded-lg">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-50"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        {title}
      </button>
      {open && <div className="px-3 pb-3">{children}</div>}
    </div>
  );
}

/**
 * How this child's prompt is assembled from its parent (agents/inheritance.py
 * `effective_prompt`): the parent's sections it keeps, as it labels its own
 * (new, replacing a parent heading, extending one with `{{parent}}`, or
 * removing it with `{{remove}}`), and the merged text a run actually sees.
 * The own instructions text itself is still edited on the Config tab; this is
 * a read only view of how it combines with the parent's.
 */
export default function PromptMergeView({ prompt, onEditOwnInstructions }) {
  const { t } = useI18n();
  if (!prompt) return null;
  const parentSections = prompt.parent_sections || [];
  const ownSections = prompt.own_sections || [];

  return (
    <div className="space-y-3">
      <Collapsible title={t('agentInheritance.prompt.inheritedSections', { count: parentSections.length })}>
        {parentSections.length === 0 ? (
          <p className="text-xs text-gray-400">{t('agentInheritance.prompt.noParentSections')}</p>
        ) : (
          <ul className="space-y-1 mb-3">
            {parentSections.map((s, i) => (
              <li key={`${s.heading}-${i}`} className="flex items-center gap-2 text-xs">
                <span className="font-mono text-gray-700">{s.heading}</span>
                <span className="text-gray-400">{t('agentInheritance.prompt.from', { source: s.source })}</span>
              </li>
            ))}
          </ul>
        )}
        {prompt.inherited_instructions && (
          <Collapsible title={t('agentInheritance.prompt.showFullInheritedText')}>
            <pre className="text-xs bg-gray-900 text-green-300 p-3 rounded-lg overflow-auto whitespace-pre-wrap max-h-64">
              {prompt.inherited_instructions}
            </pre>
          </Collapsible>
        )}
      </Collapsible>

      <Collapsible title={t('agentInheritance.prompt.ownSections', { count: ownSections.length })} defaultOpen>
        {ownSections.length === 0 ? (
          <p className="text-xs text-gray-400">{t('agentInheritance.prompt.noOwnSections')}</p>
        ) : (
          <ul className="space-y-1.5">
            {ownSections.map((s, i) => (
              <li key={`${s.heading}-${i}`} className="flex items-center gap-2 text-xs">
                <span className="font-mono text-gray-700">{s.heading}</span>
                <span className={`text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded border ${MODE_TONE[s.mode] || 'bg-gray-50 text-gray-500 border-gray-200'}`}>
                  {t(`agentInheritance.prompt.mode.${s.mode}`, { defaultValue: s.mode })}
                </span>
              </li>
            ))}
          </ul>
        )}
        {onEditOwnInstructions && (
          <button
            type="button"
            onClick={onEditOwnInstructions}
            className="mt-3 text-xs text-indigo-600 hover:text-indigo-800"
          >
            {t('agentInheritance.prompt.editOwnInstructions')}
          </button>
        )}
      </Collapsible>

      <Collapsible title={t('agentInheritance.prompt.effectivePreview')}>
        <pre className="text-xs bg-gray-900 text-green-300 p-3 rounded-lg overflow-auto whitespace-pre-wrap max-h-96">
          {prompt.effective || ''}
        </pre>
      </Collapsible>
    </div>
  );
}
