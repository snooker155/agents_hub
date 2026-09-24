/**
 * A CodeMirror 6 editor for the Code panel. Plain, controlled-ish wrapper: the
 * document is created once per (language, theme) pair, external `value`
 * changes (switching to a different snippet or version) are pushed in without
 * tearing the editor down, and every keystroke is reported back through
 * `onChange`.
 */
import { useEffect, useRef } from 'react';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap, lineNumbers, highlightActiveLine } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { indentOnInput, bracketMatching } from '@codemirror/language';
import { cmHighlightExtension, loadLanguageExtension } from '../../lib/highlight';

const LIGHT_THEME = EditorView.theme({
  '&': { backgroundColor: '#ffffff', color: '#111827', fontSize: '0.8rem' },
  '.cm-content': { caretColor: '#4f46e5' },
  '.cm-gutters': { backgroundColor: '#f9fafb', color: '#9ca3af', border: 'none' },
  '.cm-activeLine': { backgroundColor: 'rgba(79, 70, 229, 0.06)' },
  '.cm-activeLineGutter': { backgroundColor: 'rgba(79, 70, 229, 0.08)' },
});

const DARK_THEME = EditorView.theme({
  '&': { backgroundColor: '#0f172a', color: '#e5e7eb', fontSize: '0.8rem' },
  '.cm-content': { caretColor: '#a5b4fc' },
  '.cm-gutters': { backgroundColor: '#0b1220', color: '#6b7280', border: 'none' },
  '.cm-activeLine': { backgroundColor: 'rgba(165, 180, 252, 0.08)' },
  '.cm-activeLineGutter': { backgroundColor: 'rgba(165, 180, 252, 0.1)' },
}, { dark: true });

export default function CodeEditor({ value = '', onChange, language = '', dark = false, readOnly = false, className = '' }) {
  const hostRef = useRef(null);
  const viewRef = useRef(null);
  const onChangeRef = useRef(onChange);
  const valueRef = useRef(value);
  // Kept current after every render (not during it: mutating a ref while
  // rendering is what react-hooks/refs warns against), so the async editor
  // setup below and its update listener always see the latest callback/value
  // without needing either in their effect's dependency list.
  useEffect(() => {
    onChangeRef.current = onChange;
    valueRef.current = value;
  });

  // (Re)created whenever the language, theme or read-only-ness changes — all
  // three are baked into the extensions array at construction time in
  // CodeMirror 6, so there is no cheaper way to swap them.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [langExt, hlExt] = await Promise.all([
        loadLanguageExtension(language),
        cmHighlightExtension(),
      ]);
      if (cancelled || !hostRef.current) return;
      const extensions = [
        lineNumbers(),
        history(),
        highlightActiveLine(),
        indentOnInput(),
        bracketMatching(),
        keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
        hlExt,
        dark ? DARK_THEME : LIGHT_THEME,
        EditorView.lineWrapping,
        EditorState.readOnly.of(readOnly),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) onChangeRef.current?.(update.state.doc.toString());
        }),
      ];
      if (langExt) extensions.push(langExt);
      const view = new EditorView({
        state: EditorState.create({ doc: valueRef.current || '', extensions }),
        parent: hostRef.current,
      });
      viewRef.current = view;
    })();
    return () => {
      cancelled = true;
      viewRef.current?.destroy();
      viewRef.current = null;
    };
  }, [language, dark, readOnly]);

  // External value change (a different snippet or version was loaded): push it
  // in without recreating the editor, and only when it actually differs from
  // what the editor holds — this effect also fires after the editor's own
  // onChange updates the caller's state, and re-dispatching an identical
  // document would reset the cursor on every keystroke.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current === (value || '')) return;
    view.dispatch({ changes: { from: 0, to: current.length, insert: value || '' } });
  }, [value]);

  return <div ref={hostRef} className={className} />;
}
