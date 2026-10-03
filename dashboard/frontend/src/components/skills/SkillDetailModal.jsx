/**
 * One skill opened from its card: what the card leaves out because it is too
 * long to scan in a grid. The steps, the instructions rendered as markdown,
 * the bundled files and, when the safety review flagged anything, every flag
 * with the excerpt it matched. `actions` is the footer the caller fills in
 * (Edit for a workspace skill, Install for a global one).
 */
import { useEffect } from 'react';
import { FileText, GraduationCap, ListOrdered, ShieldAlert, Tag, Terminal, X } from 'lucide-react';
import ChatMarkdown from '../chat/ChatMarkdown';
import { useI18n } from '../../i18n';

const SEVERITY_TEXT = {
  high: 'text-red-700',
  medium: 'text-amber-700',
};

export default function SkillDetailModal({ skill, subtitle, onClose, actions }) {
  const { t } = useI18n();
  const steps = skill.steps || [];
  const flags = skill.safety?.flags || [];
  const scripts = skill.safety?.scripts || [];
  const resources = skill.resources || [];

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[90vh] flex flex-col"
        role="dialog" aria-modal="true" aria-label={skill.name}
      >
        <div className="flex items-start justify-between gap-3 px-6 py-4 border-b border-gray-100">
          <div className="min-w-0">
            <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
              <GraduationCap className="w-5 h-5 text-indigo-600 shrink-0" />
              <span className="truncate">{skill.name}</span>
            </h3>
            {subtitle && <div className="text-xs text-gray-400 mt-0.5">{subtitle}</div>}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('common.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto p-6 space-y-5">
          <section>
            <h4 className="text-xs font-semibold text-gray-600 mb-1">{t('skillsCatalog.whenToUseIt')}</h4>
            <p className="text-sm text-gray-700 whitespace-pre-wrap">
              {skill.description || t('skillsCatalog.noDescription')}
            </p>
            {(skill.tags || []).length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {skill.tags.map((tag) => (
                  <span key={tag} className="text-[10px] bg-white border border-gray-200 px-1.5 py-0.5 rounded text-gray-600 inline-flex items-center gap-1">
                    <Tag className="w-2.5 h-2.5" /> {tag}
                  </span>
                ))}
              </div>
            )}
          </section>

          {(flags.length > 0 || scripts.length > 0) && (
            <section className="bg-red-50 border border-red-100 rounded-lg p-3" aria-label={t('skillsCatalog.safety.flaggedTitle')}>
              <h4 className="text-xs font-semibold text-red-700 mb-2 flex items-center gap-1.5">
                <ShieldAlert className="w-3.5 h-3.5" /> {t('skillsCatalog.safety.flagged', { count: flags.length })}
              </h4>
              {flags.length > 0 && (
                <ul className="text-xs space-y-2">
                  {flags.map((f, i) => (
                    <li key={i} className="text-gray-700">
                      <span className={`font-semibold uppercase ${SEVERITY_TEXT[f.severity] || 'text-gray-500'}`}>{f.severity}</span>
                      {' · '}<span className="font-mono">{f.code}</span>{' · '}{f.where}
                      <div className="text-gray-600">{f.detail}</div>
                      {f.excerpt && <div className="text-gray-500 font-mono break-all">{f.excerpt}</div>}
                    </li>
                  ))}
                </ul>
              )}
              {scripts.length > 0 && (
                <p className="text-xs text-amber-800 mt-2 flex items-start gap-1.5">
                  <Terminal className="w-3.5 h-3.5 shrink-0 mt-px" />
                  {t('skillsCatalog.safety.scriptsTitle', { files: scripts.join(', ') })}
                </p>
              )}
            </section>
          )}

          {steps.length > 0 && (
            <section>
              <h4 className="text-xs font-semibold text-gray-600 mb-1 flex items-center gap-1.5">
                <ListOrdered className="w-3.5 h-3.5" /> {t('skillsCatalog.stepCount', { count: steps.length })}
              </h4>
              <ol className="pl-5 list-decimal space-y-1 text-sm text-gray-700">
                {steps.map((step, i) => <li key={i}>{step}</li>)}
              </ol>
            </section>
          )}

          {skill.body && (
            <section>
              <h4 className="text-xs font-semibold text-gray-600 mb-1">{t('skillsCatalog.instructionsTitle')}</h4>
              <div className="text-sm border border-gray-100 rounded-lg p-4 bg-gray-50/50 overflow-x-auto">
                <ChatMarkdown content={skill.body} />
              </div>
            </section>
          )}

          {resources.length > 0 && (
            <section>
              <h4 className="text-xs font-semibold text-gray-600 mb-1">{t('skillsCatalog.files')}</h4>
              <ul className="text-xs text-gray-600 space-y-0.5">
                {resources.map((path) => (
                  <li key={path} className="flex items-center gap-1.5 font-mono">
                    <FileText className="w-3 h-3 text-gray-400 shrink-0" /> {path}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        {actions && (
          <div className="flex justify-end gap-2 px-6 py-4 border-t border-gray-100">{actions}</div>
        )}
      </div>
    </div>
  );
}
