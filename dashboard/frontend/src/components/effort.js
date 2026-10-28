/**
 * Reasoning effort helpers shared by the pickers (EffortSelect.jsx) and the
 * pages that show an effort (providers/reasoning_profile.py).
 */

export const effortLabel = (t, value) => (value ? t(`effort.levels.${value}`) : '');

// Lowest first, as providers/reasoning_profile.EFFORT_ORDER.
const ORDER = ['off', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

/** What `value` runs at on a model with this profile, as the server clamps it:
 * itself when accepted, else the nearest accepted below, else the lowest.
 * Off is what the profile sends for off (null: nothing, so "off"). */
export function clampEffort(profile, value) {
  if (!profile || !value || !ORDER.includes(value)) return null;
  if (value === 'off') return profile.off || 'off';
  const efforts = profile.efforts || [];
  if (efforts.includes(value)) return value;
  const below = efforts.filter((e) => ORDER.indexOf(e) < ORDER.indexOf(value));
  return below.length ? below[below.length - 1] : (efforts[0] || null);
}

/** "Inherit: Medium (model default)" for the empty choice, from an
 * effective_effort answer (value + source). */
export function inheritText(t, effective) {
  if (!effective?.value) return t('effort.inherit');
  return t('effort.inheritValue', {
    value: effortLabel(t, effective.value),
    source: t(`effort.sources.${effective.source || 'provider'}`),
  });
}
