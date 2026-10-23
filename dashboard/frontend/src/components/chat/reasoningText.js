/**
 * Pulls the readable text out of a reasoning step, whatever shape the provider sent it in.
 */

// ---------------------------------------------------------------------------
// Reasoning steps — think / plan calls rendered inline in execution order
// ---------------------------------------------------------------------------

// The reasoning tools (think/plan) are pass-through scratchpads, but the agent
// framework delivers their argument as a stringified payload — sometimes plain
// text, sometimes a JSON object like {"thought": "..."} or {"plan": "..."}.
// Extract the human-readable text so we never show raw JSON to the user.
// Models often open a thought (or a reasoning summary part) with one or two
// empty lines; shown with pre-wrap they push the text down. Drop blank lines
// at the start and whitespace at the end, keep the first line's indentation.
function tidyThought(raw) {
  if (raw == null) return '';
  return String(raw).replace(/^(?:[ \t]*\r?\n)+/, '').trimEnd();
}

function parseReasoningContent(raw) {
  return tidyThought(rawReasoningContent(raw));
}

function rawReasoningContent(raw) {
  if (raw == null) return '';
  const text = typeof raw === 'string' ? raw : String(raw);
  const trimmed = text.trim();
  if (!(trimmed.startsWith('{') || trimmed.startsWith('['))) return text;
  try {
    const obj = JSON.parse(trimmed);
    if (typeof obj === 'string') return obj;
    if (obj && typeof obj === 'object') {
      // Prefer the known field names, then fall back to the first string value.
      for (const key of ['thought', 'plan', 'content', 'text', 'input']) {
        if (typeof obj[key] === 'string') return obj[key];
      }
      const firstStr = Object.values(obj).find((v) => typeof v === 'string');
      if (firstStr != null) return firstStr;
    }
  } catch {
    // Not valid JSON — fall through and show the original text.
  }
  return text;
}

export { parseReasoningContent, tidyThought };
