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

/**
 * Copy handler for a prompt bubble: a selection that runs to the end of the
 * bubble (drag past the last word, triple click) takes the block's line break
 * with it, so the pasted text ends in a newline. When everything selected is
 * the bubble's own text, copy it without the trailing whitespace. A selection
 * reaching into other messages keeps the browser's own copy.
 */
export function copyBubbleText(event) {
  const selection = window.getSelection?.();
  if (!selection || selection.rangeCount === 0) return;
  const text = selection.toString();
  const trimmed = text.replace(/\s+$/, '');
  if (!trimmed || trimmed === text) return;
  const bubble = event.currentTarget;
  const range = selection.getRangeAt(0);
  const inside = document.createRange();
  inside.selectNodeContents(bubble);
  if (range.compareBoundaryPoints(Range.START_TO_START, inside) > 0) {
    inside.setStart(range.startContainer, range.startOffset);
  }
  if (range.compareBoundaryPoints(Range.END_TO_END, inside) < 0) {
    inside.setEnd(range.endContainer, range.endOffset);
  }
  if (inside.toString().replace(/\s+$/, '') !== trimmed) return;
  event.preventDefault();
  event.clipboardData.setData('text/plain', trimmed);
}

export default trimBubbleText;
