import { useI18n } from '../../i18n';
import { useThemeColors } from '../../lib/themeColors';
import { DTYPE_COLOR_SPEC, DTYPE_KEYS, dtypeColorKey } from './graph';

/** The dtype colours actually present in this graph, in legend order. */
export default function DtypeLegend({ nodes }) {
  const { t } = useI18n();
  const colors = useThemeColors(DTYPE_COLOR_SPEC);
  const present = new Set((nodes || []).map((n) => dtypeColorKey(n.dtype)));
  const keys = DTYPE_KEYS.filter((k) => present.has(k));
  if (!keys.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-600" data-testid="dtype-legend">
      <span className="font-medium text-gray-500">{t('modelStructure.legend')}</span>
      {keys.map((k) => (
        <span key={k} className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: colors[k] }} />
          {t(`modelStructure.dtype.${k}`)}
        </span>
      ))}
    </div>
  );
}
