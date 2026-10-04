import { Headphones, Calculator, Award, Package, CheckCircle2, XCircle } from 'lucide-react';
import { useI18n } from '../../i18n';

// kit.icon is a plain word from kit.yaml; a handful get their own glyph, the
// rest fall back to a generic one rather than guessing at an icon name.
const ICONS = { headset: Headphones, calculator: Calculator, badge: Award };

const KitCard = ({ kit, onInstall, installing }) => {
  const { t } = useI18n();
  const Icon = ICONS[kit.icon] || Package;
  const required = kit.connectors?.required || [];
  const optional = kit.connectors?.optional || [];

  const Chip = ({ c }) => (
    <span
      className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border ${
        c.configured ? 'bg-green-50 text-green-700 border-green-200' : 'bg-amber-50 text-amber-700 border-amber-200'
      }`}
      title={c.configured ? t('marketplace.kits.configured') : t('marketplace.kits.notConfigured')}
    >
      {c.configured ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
      {c.name}
    </span>
  );

  return (
    <div className="bg-white rounded-lg border border-gray-100 p-4 shadow-sm hover:shadow-md transition-all flex flex-col">
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-start gap-2 min-w-0 flex-1">
          <div className="p-1.5 rounded bg-indigo-50 text-indigo-600">
            <Icon className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-gray-900 truncate">{kit.name}</div>
            <div className="text-[11px] text-gray-400 truncate">{kit.industry}</div>
          </div>
        </div>
      </div>

      <p className="text-xs text-gray-500 leading-relaxed mb-3 line-clamp-3 min-h-[3em]">{kit.description}</p>

      <div className="mb-3">
        <div className="text-[10px] uppercase font-semibold text-gray-400 mb-1">
          {t('marketplace.kits.connectors')}
        </div>
        <div className="flex flex-wrap gap-1">
          {required.map((c) => <Chip key={c.name} c={c} />)}
          {optional.map((c) => <Chip key={c.name} c={c} />)}
        </div>
      </div>

      <div className="mt-auto">
        <button
          onClick={() => onInstall(kit)}
          disabled={installing}
          className="w-full inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 transition-colors"
        >
          {t('marketplace.kits.install')}
        </button>
      </div>
    </div>
  );
};

export default KitCard;
