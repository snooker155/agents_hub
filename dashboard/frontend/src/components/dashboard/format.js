/** Number formatters shared by the Dashboard's widgets. */

export const fmtInt = (n) => (n || 0).toLocaleString();

export const fmtUsd = (n) => {
  const v = Number(n || 0);
  if (v > 0 && v < 0.01) return '< $0.01';
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

export const fmtMs = (ms) => {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
  if (ms < 3600000) {
    const m = Math.floor(ms / 60000);
    const s = Math.round((ms % 60000) / 1000);
    return m < 10 && s ? `${m} min ${s} s` : `${m} min`;
  }
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return m ? `${h} h ${m} min` : `${h} h`;
};

export const fmtBytes = (n) => {
  const b = Number(n || 0);
  if (b < 1024) return `${b} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = b / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
};

export const fmtTokens = (n) => {
  const v = Number(n || 0);
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(v >= 1e4 ? 0 : 1)}K`;
  return String(v);
};

/** "5 min ago" in the interface language, from an ISO time. */
export const fmtAgo = (iso, language, now = Date.now()) => {
  const at = iso ? new Date(iso).getTime() : NaN;
  if (Number.isNaN(at)) return '';
  const s = Math.round((at - now) / 1000);
  const rtf = new Intl.RelativeTimeFormat(language, { numeric: 'auto', style: 'short' });
  const abs = Math.abs(s);
  if (abs < 60) return rtf.format(s, 'second');
  if (abs < 3600) return rtf.format(Math.round(s / 60), 'minute');
  if (abs < 86400) return rtf.format(Math.round(s / 3600), 'hour');
  return rtf.format(Math.round(s / 86400), 'day');
};

/** Where a run of the given kind opens: its own page, on that run. */
export const runHref = (item) => {
  const run = encodeURIComponent(item.run_id || '');
  const entity = encodeURIComponent(item.entity_id || '');
  switch (item.kind) {
    case 'agent': return item.run_id ? `/messages/${run}` : null;
    case 'flow': return `/flows/${entity}?run=${run}`;
    case 'team': return `/teams/${entity}?run=${run}`;
    case 'loop': return `/loops?loop=${entity}&run=${run}`;
    case 'scenario': return `/playground/${entity}?run=${run}`;
    default: return null;
  }
};
