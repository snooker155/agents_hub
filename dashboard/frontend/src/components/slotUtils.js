// Helpers for structured-memory slot values, kept out of SlotValue.jsx so that
// file only exports components (react-refresh/only-export-components).

export const SLOT_VALUE_MAX = 80;   // chars shown before a scalar is truncated
export const SLOT_CHILD_LIMIT = 8;  // children shown before a container is truncated

// A value the agent wrote as a JSON string is shown as the object it encodes.
export function coerceSlotValue(v) {
  if (typeof v !== 'string') return v;
  const s = v.trim();
  const looksJson = (s.startsWith('{') && s.endsWith('}')) || (s.startsWith('[') && s.endsWith(']'));
  if (!looksJson) return v;
  try {
    const parsed = JSON.parse(s);
    return parsed !== null && typeof parsed === 'object' ? parsed : v;
  } catch {
    return v;
  }
}

export const isSlotContainer = (v) => v !== null && typeof v === 'object';

export function slotScalarText(v) {
  if (v === null || v === undefined) return 'null';
  return typeof v === 'string' ? v : JSON.stringify(v);
}

// "{3 fields}" / "[5 items]" — the collapsed form of a nested value.
export function slotContainerSummary(v) {
  const n = Array.isArray(v) ? v.length : Object.keys(v).length;
  const noun = Array.isArray(v) ? 'item' : 'field';
  const body = `${n} ${noun}${n === 1 ? '' : 's'}`;
  return Array.isArray(v) ? `[${body}]` : `{${body}}`;
}
