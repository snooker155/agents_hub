/**
 * How every chat composer grows: with the text, one line at a time, up to
 * CHAT_MAX_LINES, then it scrolls inside. The cap is measured from the box's
 * own line height, padding and border, so a font change keeps it in lines
 * and a bordered box does not lose its last line to the border.
 *
 * Called from an effect on the composer's text, so a message typed, one put
 * there by a button (Discuss, a run report) and the box emptied after a send
 * all size the same way.
 */
export const CHAT_MAX_LINES = 10;

export function autoGrowTextarea(ta, maxLines = CHAT_MAX_LINES) {
  if (!ta || typeof window === 'undefined') return;
  const style = window.getComputedStyle(ta);
  const fontSize = parseFloat(style.fontSize) || 14;
  const line = parseFloat(style.lineHeight) || fontSize * 1.5;
  const padding = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
  const border = (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0);
  const max = Math.round(line * maxLines + padding + border);
  ta.style.height = 'auto';
  // scrollHeight is the content plus padding; a border-box height needs the border too.
  const wanted = ta.scrollHeight + (style.boxSizing === 'border-box' ? border : 0);
  ta.style.height = `${Math.min(wanted, max)}px`;
  ta.style.overflowY = wanted > max ? 'auto' : 'hidden';
}
