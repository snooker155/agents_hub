/**
 * The note under the provider keys after a save switched a model on
 * (common/default_model.py): which model, at what price, and where to change it.
 * Model choice stays on the Models page; Settings only reports the step.
 */
import { Link } from 'react-router-dom';
import { Sparkles } from 'lucide-react';
import { useI18n } from '../../i18n';

const price = (n) => Number(n || 0).toFixed(2).replace(/\.00$/, '');

export default function DefaultModelNote({ models }) {
  const { t } = useI18n();
  if (!models || !models.length) return null;
  return (
    <div className="space-y-2" role="status">
      {models.map((m) => (
        <div key={m.provider} className="flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs text-emerald-900">
          <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            {t('settings.defaultModelNote.text', {
              model: m.model, input: price(m.input_price), output: price(m.output_price),
            })}{' '}
            <Link to="/models" className="font-medium underline">{t('settings.defaultModelNote.link')}</Link>
          </span>
        </div>
      ))}
    </div>
  );
}
