/**
 * A result the system loop wrote carries one machine readable marker line
 * before its markdown body:
 *
 *   <!-- system-patch {"branch": "...", "repo_dir": "...", "commit": "...", "fetch_command": "..."} -->
 *   ## System patch
 *   ```diff
 *   ...
 *   ```
 *
 * This is the only place that marker's shape is known, so both the parsing
 * and the "is this a system patch result" question live here, pure and
 * dependency free, rather than inline in TaskDetails.
 */

const MARKER_RE = /^<!--\s*system-patch\s+(\{[\s\S]*?\})\s*-->/;

/**
 * `text` is one result entry's raw string. Returns `null` when the first
 * line is not the marker (an ordinary result), otherwise `{ meta, body }`
 * where `meta` is the parsed JSON object and `body` is the text with the
 * marker line (and the blank line after it, if any) removed, so callers can
 * hand `body` to the markdown renderer without the comment leaking through.
 */
export function parseSystemPatch(text) {
  const s = String(text ?? '');
  const match = MARKER_RE.exec(s.trimStart());
  if (!match) return null;
  let meta;
  try {
    meta = JSON.parse(match[1]);
  } catch {
    return null;
  }
  if (!meta || typeof meta !== 'object') return null;
  const rest = s.trimStart().slice(match[0].length);
  const body = rest.replace(/^\s*\n/, '');
  return { meta, body };
}
