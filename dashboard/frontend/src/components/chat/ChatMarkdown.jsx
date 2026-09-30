/**
 * A chat reply rendered as markdown: GitHub flavour (tables, strikethrough,
 * task lists, autolinks), single newlines kept as line breaks the way people
 * and models write in a chat, fenced code as a CodeBlock, and formulas in any
 * of the usual LaTeX delimiters through KaTeX (see mathMarkdown.js). KaTeX is
 * loaded the first time a reply has a formula; until then it shows as code.
 *
 * It renders from the first token: `streaming` closes whatever markup the
 * reply is cut in the middle of (see streamingMarkdown.js), so a turn that is
 * still arriving already looks like the finished one. Raw HTML in a reply is
 * shown as text, never injected.
 *
 * `citations` turns the reply's `[n]` markers into buttons that scroll to the
 * source they name; `[n]` inside code stays code. `codeActions` adds buttons
 * to every code block's header (the Chat page's "Open in Code panel").
 */
import { useContext, useEffect, useMemo, useState } from 'react';
import Markdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import { BookmarkPlus, Check, Code2, Loader2 } from 'lucide-react';
import CodeBlock from '../CodeBlock';
import { useI18n } from '../../i18n';
import { trimBubbleText } from '../../lib/chatText';
import { closeOpenMarkdown } from './streamingMarkdown';
import { hasMath, holdOpenMath, normalizeMath } from './mathMarkdown';
import { ChatCodeActionsContext, ChatMathActionsContext, CitationFocusContext } from './chatMarkdownContext';
import './chatMarkdown.css';

const MARKER_RE = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
const SKIP_TAGS = new Set(['code', 'pre', 'a']);

// Rehype plugin: `[n]` / `[n, m]` in text nodes, for known sources only,
// become <cite-ref> elements (rendered by the `cite-ref` component below).
function rehypeCitations({ known }) {
  const split = (value) => {
    const out = [];
    let last = 0;
    value.replace(MARKER_RE, (match, nums, offset) => {
      const numbers = nums.split(',').map((s) => Number(s.trim()));
      if (!numbers.every((n) => known.has(n))) return match;
      if (offset > last) out.push({ type: 'text', value: value.slice(last, offset) });
      for (const n of numbers) {
        out.push({
          type: 'element', tagName: 'cite-ref', properties: { dataN: String(n) },
          children: [{ type: 'text', value: String(n) }],
        });
      }
      last = offset + match.length;
      return match;
    });
    if (!out.length) return null;
    if (last < value.length) out.push({ type: 'text', value: value.slice(last) });
    return out;
  };
  const walk = (node) => {
    if (!node.children) return;
    const next = [];
    for (const child of node.children) {
      if (child.type === 'text') {
        const parts = split(child.value);
        if (parts) next.push(...parts); else next.push(child);
      } else {
        if (child.type !== 'element' || !SKIP_TAGS.has(child.tagName)) walk(child);
        next.push(child);
      }
    }
    node.children = next;
  };
  return (tree) => { if (known && known.size) walk(tree); };
}

const isMathCode = (node) => node?.type === 'element' && node.tagName === 'code'
  && [].concat(node.properties?.className || []).some((c) => c === 'language-math' || c === 'math-display');

// Rehype plugin, run before rehype-katex: a display formula (`$$` block or a
// ```math fence) is wrapped in <math-block> carrying its TeX, so the
// `math-block` component below can offer to save it. KaTeX then replaces the
// <pre> inside with the rendered formula.
function rehypeMathBlocks() {
  const walk = (node) => {
    (node.children || []).forEach((child, i) => {
      if (child.type !== 'element') return;
      const code = child.tagName === 'pre' && child.children?.find((c) => c.type === 'element');
      if (code && isMathCode(code)) {
        node.children[i] = {
          type: 'element', tagName: 'math-block',
          properties: { dataTex: hastText(code).trim() }, children: [child],
        };
      } else {
        walk(child);
      }
    });
  };
  return (tree) => walk(tree);
}

function hastText(node) {
  if (!node) return '';
  if (node.type === 'text') return node.value || '';
  return (node.children || []).map(hastText).join('');
}

function OpenInPanelButton({ code, language }) {
  const { t } = useI18n();
  const actions = useContext(ChatCodeActionsContext);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  if (!actions) return null;
  const open = () => {
    setBusy(true);
    setFailed(false);
    Promise.resolve(actions.open(code, language))
      .catch(() => setFailed(true))
      .finally(() => setBusy(false));
  };
  return (
    <button
      type="button"
      onClick={open}
      disabled={busy}
      className="hl-code-block__action"
      title={failed ? t('chat.codeBlock.openFailed') : t('chat.codeBlock.openInPanelHint')}
      data-testid="code-open-in-panel"
    >
      {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Code2 className="w-3.5 h-3.5" />}
      <span>{failed ? t('chat.codeBlock.openFailed') : t('chat.codeBlock.openInPanel')}</span>
    </button>
  );
}

function FencedBlock({ node }) {
  const codeNode = (node?.children || []).find((c) => c.tagName === 'code');
  const classes = [].concat(codeNode?.properties?.className || []);
  const language = (classes.find((c) => String(c).startsWith('language-')) || '').slice('language-'.length);
  const code = hastText(codeNode).replace(/\n$/, '');
  return (
    <CodeBlock
      language={language}
      code={code}
      actions={<OpenInPanelButton code={code} language={language} />}
    />
  );
}

// Keep a display formula in the user's personal memory, under a title they
// give it: the TeX source is what is saved, so the agent reads it back as-is.
function SaveFormula({ tex }) {
  const { t } = useI18n();
  const actions = useContext(ChatMathActionsContext);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState('');
  const [state, setState] = useState('idle');
  if (!actions || !tex) return null;
  const save = (e) => {
    e.preventDefault();
    setState('saving');
    Promise.resolve(actions.save(tex, title.trim() || t('chat.mathBlock.defaultTitle')))
      .then(() => { setState('saved'); setEditing(false); })
      .catch(() => setState('failed'));
  };
  if (state === 'saved') {
    return (
      <span className="chat-md__math-action chat-md__math-action--done" data-testid="formula-saved">
        <Check className="w-3.5 h-3.5" /> {t('chat.mathBlock.saved')}
      </span>
    );
  }
  if (!editing) {
    return (
      <button
        type="button"
        className="chat-md__math-action"
        onClick={() => { setEditing(true); setState('idle'); }}
        title={t('chat.mathBlock.saveHint')}
        data-testid="formula-save"
      >
        <BookmarkPlus className="w-3.5 h-3.5" /> {t('chat.mathBlock.save')}
      </button>
    );
  }
  return (
    <form className="chat-md__math-form" onSubmit={save}>
      <input
        autoFocus
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Escape') setEditing(false); }}
        placeholder={t('chat.mathBlock.titlePlaceholder')}
        aria-label={t('chat.mathBlock.titlePlaceholder')}
        data-testid="formula-title"
      />
      <button type="submit" disabled={state === 'saving'}>
        {state === 'saving' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : t('chat.mathBlock.confirm')}
      </button>
      <button type="button" onClick={() => setEditing(false)}>{t('chat.mathBlock.cancel')}</button>
      {state === 'failed' ? <span className="chat-md__math-error">{t('chat.mathBlock.failed')}</span> : null}
    </form>
  );
}

function MathBlock({ node, children }) {
  return (
    <div className="chat-md__math">
      {children}
      <SaveFormula tex={node?.properties?.dataTex} />
    </div>
  );
}

function CiteRef({ node }) {
  const { t } = useI18n();
  const onCite = useContext(CitationFocusContext);
  const n = Number(node?.properties?.dataN);
  return (
    <button
      type="button"
      onClick={() => onCite?.(n)}
      className="mx-0.5 inline-flex items-center rounded px-1 text-[11px] font-semibold leading-4 align-baseline bg-indigo-50 text-indigo-700 hover:bg-indigo-100"
      title={t('files.citations.source', { n })}
      data-testid="citation-marker"
    >
      {n}
    </button>
  );
}

const InlineCode = ({ children }) => <code className="chat-md__code">{children}</code>;
const Link = ({ href, children }) => <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
const Table = ({ children }) => <div className="chat-md__table"><table>{children}</table></div>;

// Single dollars are off: normalizeMath has already turned every formula it
// recognised into `$$`, so a `$` left over is a price.
const REMARK_PLUGINS = [remarkGfm, remarkBreaks, [remarkMath, { singleDollarTextMath: false }]];
const KATEX_OPTIONS = { throwOnError: false, strict: false, errorColor: 'var(--danger, #dc2626)' };

let katexPlugin = null;
let katexLoading = null;

function loadKatex() {
  if (!katexLoading) {
    katexLoading = Promise.all([import('rehype-katex'), import('katex/dist/katex.min.css')])
      .then(([mod]) => { katexPlugin = mod.default; return katexPlugin; })
      .catch((err) => { katexLoading = null; throw err; });
  }
  return katexLoading;
}

// rehype-katex once some reply needs it; null until then.
function useKatex(needed) {
  const [plugin, setPlugin] = useState(() => katexPlugin);
  useEffect(() => {
    if (!needed || plugin) return undefined;
    let alive = true;
    loadKatex().then((p) => { if (alive) setPlugin(() => p); }, () => {});
    return () => { alive = false; };
  }, [needed, plugin]);
  return plugin;
}
// One map for every reply: a new map makes react-markdown remount each element.
const COMPONENTS = {
  pre: FencedBlock,
  code: InlineCode,
  a: Link,
  table: Table,
  'cite-ref': CiteRef,
  'math-block': MathBlock,
};

export default function ChatMarkdown({ content, streaming = false, citations = null, onCite = null }) {
  const text = useMemo(() => {
    const trimmed = trimBubbleText(content);
    return normalizeMath(streaming ? closeOpenMarkdown(holdOpenMath(trimmed)) : trimmed);
  }, [content, streaming]);
  const katex = useKatex(hasMath(text));
  const known = useMemo(
    () => new Set((citations || []).map((c) => Number(c.n))),
    [citations],
  );
  // Citations first: they skip `code`, which is where a formula still is then.
  const rehypePlugins = useMemo(
    () => [[rehypeCitations, { known }], rehypeMathBlocks, ...(katex ? [[katex, KATEX_OPTIONS]] : [])],
    [known, katex],
  );
  return (
    <CitationFocusContext.Provider value={onCite}>
      <div className="chat-md">
        <Markdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={rehypePlugins} components={COMPONENTS}>
          {text}
        </Markdown>
      </div>
    </CitationFocusContext.Provider>
  );
}
