/**
 * Read-only syntax highlighting for fenced code blocks.
 *
 * MarkdownRenderer and CodeView both need to turn a language name and a body
 * string into highlighted React spans, without paying for a full CodeMirror
 * editor when the block is never edited. This wraps `@lezer/highlight`
 * directly: parse the body with the language's Lezer parser, walk the
 * resulting tree with `highlightTree`, and turn each highlighted range into a
 * `<span className="tok-...">`. The editor (CodeEditor.jsx) uses the real
 * CodeMirror `language()` extension for the same languages; this module only
 * covers static rendering.
 *
 * Everything here is loaded lazily (dynamic `import()` from the caller) so the
 * language packages never land in the main bundle for a page that shows no
 * code.
 */
import React from 'react';
import { highlightTree, tagHighlighter, tags as t } from '@lezer/highlight';

// One class per tag bucket; colours live in highlight.css (light + dark).
// Shared between the read-only path (`tagHighlighter`, below — a plain,
// non-CodeMirror `Highlighter` usable with `highlightTree` directly, so no
// editor instance is needed for a static block) and the live editor
// (`cmHighlightExtension`, which feeds the same list to CodeMirror's own
// `HighlightStyle`), so a token reads the same colour in both places.
const TAG_CLASS_SPECS = [
  { tag: t.comment, class: 'tok-comment' },
  { tag: t.lineComment, class: 'tok-comment' },
  { tag: t.blockComment, class: 'tok-comment' },
  { tag: t.string, class: 'tok-string' },
  { tag: t.special(t.string), class: 'tok-string' },
  { tag: t.regexp, class: 'tok-string' },
  { tag: t.number, class: 'tok-number' },
  { tag: t.bool, class: 'tok-keyword' },
  { tag: t.null, class: 'tok-keyword' },
  { tag: t.keyword, class: 'tok-keyword' },
  { tag: t.controlKeyword, class: 'tok-keyword' },
  { tag: t.operatorKeyword, class: 'tok-keyword' },
  { tag: t.moduleKeyword, class: 'tok-keyword' },
  { tag: t.definitionKeyword, class: 'tok-keyword' },
  { tag: t.className, class: 'tok-type' },
  { tag: t.typeName, class: 'tok-type' },
  { tag: t.namespace, class: 'tok-type' },
  { tag: t.function(t.variableName), class: 'tok-function' },
  { tag: t.function(t.definition(t.variableName)), class: 'tok-function' },
  { tag: t.definition(t.propertyName), class: 'tok-property' },
  { tag: t.propertyName, class: 'tok-property' },
  { tag: t.attributeName, class: 'tok-property' },
  { tag: t.variableName, class: 'tok-variable' },
  { tag: t.definition(t.variableName), class: 'tok-variable' },
  { tag: t.tagName, class: 'tok-tag' },
  { tag: t.angleBracket, class: 'tok-punctuation' },
  { tag: t.punctuation, class: 'tok-punctuation' },
  { tag: t.bracket, class: 'tok-punctuation' },
  { tag: t.operator, class: 'tok-operator' },
  { tag: t.meta, class: 'tok-meta' },
  { tag: t.heading, class: 'tok-heading' },
  { tag: t.strong, class: 'tok-strong' },
  { tag: t.emphasis, class: 'tok-emphasis' },
  { tag: t.link, class: 'tok-link' },
  { tag: t.url, class: 'tok-string' },
  { tag: t.invalid, class: 'tok-invalid' },
  { tag: t.atom, class: 'tok-keyword' },
  { tag: t.labelName, class: 'tok-property' },
  { tag: t.constant(t.variableName), class: 'tok-type' },
];

const HIGHLIGHTER = tagHighlighter(TAG_CLASS_SPECS);

// Language name (as written after a ``` fence, or the CodeView `language`
// field) -> loader for a Lezer/CM6 `LanguageSupport`. Aliases point at the
// same loader. Legacy modes (bash/shell/yaml) come from
// `@codemirror/legacy-modes`, wrapped with `StreamLanguage`.
const LOADERS = {
  python: () => import('@codemirror/lang-python').then((m) => m.python()),
  py: () => import('@codemirror/lang-python').then((m) => m.python()),
  javascript: () => import('@codemirror/lang-javascript').then((m) => m.javascript()),
  js: () => import('@codemirror/lang-javascript').then((m) => m.javascript()),
  jsx: () => import('@codemirror/lang-javascript').then((m) => m.javascript({ jsx: true })),
  typescript: () => import('@codemirror/lang-javascript').then((m) => m.javascript({ typescript: true })),
  ts: () => import('@codemirror/lang-javascript').then((m) => m.javascript({ typescript: true })),
  tsx: () => import('@codemirror/lang-javascript').then((m) => m.javascript({ jsx: true, typescript: true })),
  node: () => import('@codemirror/lang-javascript').then((m) => m.javascript()),
  json: () => import('@codemirror/lang-json').then((m) => m.json()),
  markdown: () => import('@codemirror/lang-markdown').then((m) => m.markdown()),
  md: () => import('@codemirror/lang-markdown').then((m) => m.markdown()),
  html: () => import('@codemirror/lang-html').then((m) => m.html()),
  css: () => import('@codemirror/lang-css').then((m) => m.css()),
  sql: () => import('@codemirror/lang-sql').then((m) => m.sql()),
  bash: () => Promise.all([
    import('@codemirror/language'),
    import('@codemirror/legacy-modes/mode/shell'),
  ]).then(([{ StreamLanguage }, { shell }]) => StreamLanguage.define(shell)),
  sh: () => Promise.all([
    import('@codemirror/language'),
    import('@codemirror/legacy-modes/mode/shell'),
  ]).then(([{ StreamLanguage }, { shell }]) => StreamLanguage.define(shell)),
  shell: () => Promise.all([
    import('@codemirror/language'),
    import('@codemirror/legacy-modes/mode/shell'),
  ]).then(([{ StreamLanguage }, { shell }]) => StreamLanguage.define(shell)),
  yaml: () => Promise.all([
    import('@codemirror/language'),
    import('@codemirror/legacy-modes/mode/yaml'),
  ]).then(([{ StreamLanguage }, { yaml }]) => StreamLanguage.define(yaml)),
  yml: () => Promise.all([
    import('@codemirror/language'),
    import('@codemirror/legacy-modes/mode/yaml'),
  ]).then(([{ StreamLanguage }, { yaml }]) => StreamLanguage.define(yaml)),
};

// Languages runnable by the backend sandbox (routes/*/code/run). Kept here so
// both the composer and CodeView agree on what "Run" applies to.
export const RUNNABLE_LANGUAGES = new Set(['python', 'py', 'node', 'javascript', 'js', 'bash', 'sh', 'shell']);

export function isRunnable(language) {
  return RUNNABLE_LANGUAGES.has(String(language || '').toLowerCase());
}

export function normalizeLanguage(language) {
  return String(language || '').trim().toLowerCase();
}

export function isKnownLanguage(language) {
  return Object.prototype.hasOwnProperty.call(LOADERS, normalizeLanguage(language));
}

// Cache of resolved language extensions so re-highlighting the same language
// (e.g. a second code block in the same message) doesn't re-import.
const languageCache = new Map();

async function loadLanguage(language) {
  const key = normalizeLanguage(language);
  const loader = LOADERS[key];
  if (!loader) return null;
  if (languageCache.has(key)) return languageCache.get(key);
  const promise = loader().catch(() => null);
  languageCache.set(key, promise);
  return promise;
}

// The same loader, exported for CodeEditor.jsx: what `@codemirror/lang-*`
// returns (a `LanguageSupport`) or `StreamLanguage.define(...)` (a `Language`)
// is already a valid CodeMirror 6 `Extension` — CodeEditor just drops it
// straight into its extensions array. `null` for an unrecognised language
// (the editor then has no language-aware behaviour, but still edits fine).
export const loadLanguageExtension = loadLanguage;

// The CodeMirror 6 counterpart of `HIGHLIGHTER` above: a `syntaxHighlighting`
// extension built from the same tag → class list, so the live editor and the
// read-only block colour tokens identically through the shared CSS classes in
// highlight.css. Lazily imports `@codemirror/language` (only the editor needs
// it) and is cached — every CodeEditor instance reuses the same extension.
let cmHighlightPromise = null;
export function cmHighlightExtension() {
  if (!cmHighlightPromise) {
    cmHighlightPromise = import('@codemirror/language').then(
      ({ HighlightStyle, syntaxHighlighting }) => syntaxHighlighting(HighlightStyle.define(TAG_CLASS_SPECS)),
    );
  }
  return cmHighlightPromise;
}

/**
 * Highlight `code` for `language`, returning an array of React nodes (spans
 * with `tok-*` classes for recognised tokens, plain strings elsewhere), or
 * `null` when the language is unknown or the parser failed — callers fall
 * back to plain text in that case.
 */
export async function highlightCode(code, language) {
  const langSupport = await loadLanguage(language);
  if (!langSupport) return null;
  // Both LanguageSupport (from lang-* packages) and a bare Language
  // (StreamLanguage.define(...)) expose `.language.parser` / `.parser`.
  const lang = langSupport.language || langSupport;
  const parser = lang?.parser;
  if (!parser || typeof parser.parse !== 'function') return null;

  let tree;
  try {
    tree = parser.parse(code);
  } catch {
    return null;
  }

  const nodes = [];
  let cursor = 0;
  let key = 0;
  const flushPlain = (to) => {
    if (to > cursor) nodes.push(code.slice(cursor, to));
  };
  try {
    highlightTree(tree, HIGHLIGHTER, (from, to, classes) => {
      flushPlain(from);
      nodes.push(
        React.createElement('span', { key: `t${key++}`, className: classes }, code.slice(from, to)),
      );
      cursor = to;
    });
  } catch {
    return null;
  }
  flushPlain(code.length);
  return nodes;
}

export default highlightCode;
