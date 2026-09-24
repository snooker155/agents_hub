import { Info } from 'lucide-react';
import { useI18n } from '../../i18n';
import { formatCount } from './graph';

// What an API model answers with: the catalogue facts, since its weights are
// not ours to read.

const has = (v) => v !== null && v !== undefined && v !== '';

export default function ModelCard({ model = {} }) {
  const { t } = useI18n();
  const na = t('modelStructure.unknown');
  const price = (v) => (has(v) && Number.isFinite(Number(v))
    ? t('modelStructure.card.perMillion', { price: `$${Number(v)}` })
    : na);
  const yesNo = (v) => (v === undefined || v === null ? na : t(v ? 'modelStructure.card.yes' : 'modelStructure.card.no'));
  let released = na;
  if (has(model.released_at)) {
    const d = new Date(model.released_at);
    released = Number.isNaN(d.getTime()) ? String(model.released_at) : d.toLocaleDateString();
  }
  const rows = [
    ['contextWindow', has(model.context_window) && Number(model.context_window) > 0
      ? t('modelStructure.card.tokens', { count: formatCount(model.context_window) }) : na],
    ['inputPrice', price(model.input_price)],
    ['outputPrice', price(model.output_price)],
    ['cachedInputPrice', price(model.cached_input_price)],
    ['releasedAt', released],
    ['enabled', yesNo(model.enabled)],
    ['default', yesNo(model.default)],
    ['priceSource', has(model.price_source) ? String(model.price_source) : na],
  ];
  return (
    <section className="max-w-2xl rounded-xl border border-gray-200 bg-white" data-testid="model-card">
      <div className="flex items-start gap-2 border-b border-gray-100 px-4 py-3 text-sm text-gray-600">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-indigo-500" />
        <p>{t('modelStructure.card.apiNote')}</p>
      </div>
      <dl className="divide-y divide-gray-100">
        {rows.map(([key, value]) => (
          <div key={key} className="grid grid-cols-2 gap-4 px-4 py-2 text-sm">
            <dt className="text-gray-500">{t(`modelStructure.card.${key}`)}</dt>
            <dd className="text-gray-900">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
