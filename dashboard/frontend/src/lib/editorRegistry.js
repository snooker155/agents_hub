/**
 * The CodeMirror views on the page, found by their DOM: something reading a
 * selection from the DOM (chat/quote.js) asks here for the editor it is in,
 * since CodeMirror draws only the lines in sight and the DOM text of a long
 * file is not the file. A WeakMap, so a closed editor leaves nothing behind.
 */
const views = new WeakMap();

/** Remembers `view` under its root element (`view.dom`, the `.cm-editor`). */
export function registerEditor(view) {
  if (view?.dom) views.set(view.dom, view);
}

/** The CodeMirror view `node` is in, or null. */
export function editorFor(node) {
  const el = node?.nodeType === 1 ? node : node?.parentElement;
  const root = el?.closest?.('.cm-editor');
  return root ? views.get(root) || null : null;
}
