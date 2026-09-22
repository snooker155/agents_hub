/**
 * Which tool calls are memory writes, and reading back what one returned.
 */

// ---------------------------------------------------------------------------
// Extraction tool cards — rich rendering for extract_from_text / save_extraction
// ---------------------------------------------------------------------------
const EXTRACTION_TOOLS = ['extract_from_text', 'save_extraction'];

function parseJsonSafe(text) {
  try { return JSON.parse(text); } catch { return null; }
}

export { EXTRACTION_TOOLS, parseJsonSafe };
