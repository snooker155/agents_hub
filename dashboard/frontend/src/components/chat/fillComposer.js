/** Puts `text` into the message box behind `textareaRef` and moves the
 *  caret to its end, so the example can be edited before sending. */
export default function fillComposer(text, setInput, textareaRef) {
  setInput(text);
  setTimeout(() => {
    const box = textareaRef.current;
    if (!box) return;
    box.focus();
    box.setSelectionRange(text.length, text.length);
  }, 0);
}
