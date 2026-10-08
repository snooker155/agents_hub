import { useI18n } from '../../i18n';
import { humanBytes } from './jobs';

// Whether a model runs on this machine and how fast, as the runtime
// estimates it (deploy/models/app.py estimate_fit): shared by the Hub search
// and the file list.

const VERDICT_CLS = {
  fits: 'bg-green-50 text-green-700 border-green-200',
  tight: 'bg-amber-50 text-amber-800 border-amber-200',
  offload: 'bg-orange-50 text-orange-700 border-orange-200',
  no: 'bg-red-50 text-red-700 border-red-200',
};

// `compact` drops the verdict's words (the colour and the tooltip carry it):
// a row of quantizations stays one line.
export function FitBadge({ fit, label, compact, testId }) {
  const { t } = useI18n();
  if (!fit) return null;
  const verdict = t(`localModels.fit.verdicts.${fit.verdict}`);
  const need = t('localModels.fit.need', { need: humanBytes(fit.need_bytes), budget: humanBytes(fit.budget_bytes) });
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs whitespace-nowrap ${VERDICT_CLS[fit.verdict] || VERDICT_CLS.no}`}
      title={compact ? `${verdict}, ${need}` : need}
      data-testid={testId}
    >
      {label && <span className="font-mono">{label}</span>}
      {(!compact || !fit.tokens_per_second) && <span className="font-medium">{verdict}</span>}
      {fit.tokens_per_second ? <span className="tabular-nums">{compact ? '' : '· '}{t('localModels.fit.speed', { n: Math.round(fit.tokens_per_second) })}</span> : null}
    </span>
  );
}

// One line on what the estimates assume: the chip or card, the memory the
// GPU may use, the bandwidth, and whether it was measured here.
export function HardwareLine({ hw, context = 4096 }) {
  const { t } = useI18n();
  if (!hw) return null;
  const base = hw.gpu_bytes
    ? t('localModels.modelSearch.hardware', { name: hw.name, ram: humanBytes(hw.ram_bytes), gpu: humanBytes(hw.gpu_bytes), bw: Math.round(hw.bandwidth_gbps) })
    : t('localModels.modelSearch.hardwareCpu', { name: hw.name, ram: humanBytes(hw.ram_bytes), bw: Math.round(hw.bandwidth_gbps) });
  return (
    <p className="text-xs text-gray-500" data-testid="hardware-line">
      {base}{' '}
      {hw.calibrated ? t('localModels.modelSearch.calibrated') : (!hw.known ? t('localModels.modelSearch.assumed') : '')}{' '}
      {t('localModels.modelSearch.note', { context })}
    </p>
  );
}
