/**
 * Text shown in a chat bubble.
 *
 * Model output almost always ends with a newline, and several bubbles render it
 * under `whitespace-pre-wrap`, where that newline is a rendered blank line: the
 * bubble grows an empty row under the last sentence. Strip trailing whitespace
 * at the point of display rather than at the point of storage, so the stored
 * transcript keeps exactly what the model produced.
 *
 * Trailing only. A leading newline is not the same defect and removing one
 * would silently reflow content the model meant to indent.
 */
export function trimBubbleText(text) {
  return String(text ?? '').replace(/\s+$/, '');
}

export default trimBubbleText;
