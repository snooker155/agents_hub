/**
 * What the first run's screens share besides components: the order of the
 * steps, provider names, and how a server error reads.
 */

export const STEPS = ['hello', 'language', 'appearance', 'model', 'choose_model', 'voice', 'search', 'memory', 'demo', 'done'];
export const MODEL_AT = STEPS.indexOf('model');

export const PROVIDER_LABELS = {
  openai: 'OpenAI', anthropic: 'Anthropic', google: 'Google Gemini', ollama: 'Ollama', lmstudio: 'LM Studio',
  'hub-local': 'Agents Hub',
};
export const CLOUD = ['openai', 'anthropic', 'google'];
export const SERVERS = ['ollama', 'lmstudio'];
/** Providers the choose step can offer tiers for. */
export const TIERED = [...CLOUD, ...SERVERS];

export const ACTIVE_JOB = new Set(['queued', 'running']);

export const fieldCls = 'w-full rounded-xl border border-gray-200 bg-white px-4 py-3 text-base text-gray-900 '
  + 'placeholder:text-gray-400 focus:outline-none focus:border-indigo-400';

/** The ready local set is running or done: the model step is met by it. */
export function localSetStarted(ctx) {
  const status = ctx?.local_set?.status;
  return ACTIVE_JOB.has(status) || status === 'done';
}

export function hasModel(ctx) {
  return (ctx?.providers || []).length > 0 || localSetStarted(ctx);
}

/** The steps shown for this hub: no model choice without a provider that has tiers. */
export function visibleSteps(ctx) {
  return STEPS.filter((s) => s !== 'choose_model' || (ctx?.providers || []).some((p) => TIERED.includes(p)));
}

/** The step before or after ``step`` (dir -1 or 1) among those shown for ``ctx``. */
export function stepFrom(ctx, step, dir) {
  const list = visibleSteps(ctx);
  const order = STEPS.indexOf(step);
  if (dir > 0) return list.find((s) => STEPS.indexOf(s) > order) || list[list.length - 1];
  return [...list].reverse().find((s) => STEPS.indexOf(s) < order) || list[0];
}

/** A model id as a person reads it: "piper-ru_RU-irina-medium" is "Piper · Irina". */
export function voiceLabel(model) {
  const m = String(model || '');
  const piper = m.match(/^piper-[a-z]{2}_[A-Z]{2}-([a-z]+)/);
  if (piper) return `Piper · ${piper[1][0].toUpperCase()}${piper[1].slice(1)}`;
  const head = m.split(/[-_]/)[0] || m;
  return head ? `${head[0].toUpperCase()}${head.slice(1)}` : '';
}

/** The server's message for a failed call, by code where the step knows it. */
export function errorText(err, t) {
  const detail = err?.response?.data?.detail;
  const code = detail && typeof detail === 'object' ? detail.code : '';
  const message = detail && typeof detail === 'object' ? detail.message : (typeof detail === 'string' ? detail : '');
  if (code) return t(`firstRun.errors.${code}`, { defaultValue: message || t('firstRun.errors.failed') });
  return message || err?.message || t('firstRun.errors.failed');
}
