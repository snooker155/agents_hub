import { useI18n } from '../../i18n';

// Formatting for the runtime's fit and speed estimates
// (deploy/models/app.py estimate_fit), shared by the Hub search and the file
// list; the badges are in FitBadge.jsx.

export function fmtParams(n) {
  if (!n) return '';
  return n >= 1e9 ? `${(n / 1e9).toFixed(n >= 1e11 ? 0 : 1)}B` : `${Math.round(n / 1e6)}M`;
}

export function fmtContext(n) {
  if (!n) return '';
  return n >= 1024 ? `${Math.round(n / 1024)}k` : String(n);
}

// "Fits · ~56 tok/s" in words, for an <option> where no badge can go.
export function useFitText() {
  const { t } = useI18n();
  return (fit) => {
    if (!fit) return '';
    const verdict = t(`localModels.fit.verdicts.${fit.verdict}`);
    return fit.tokens_per_second ? `${verdict} · ${t('localModels.fit.speed', { n: Math.round(fit.tokens_per_second) })}` : verdict;
  };
}
