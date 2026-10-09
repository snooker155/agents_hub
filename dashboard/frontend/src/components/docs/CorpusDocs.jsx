/**
 * Docs sections rendered from the documentation corpus: changelog, guides, references and the FAQ.
 */
import { ArrowRight, BookOpen } from 'lucide-react';
import { useEffect, useState } from 'react';
import Markdown from 'react-markdown';
import { Link } from 'react-router-dom';
import remarkGfm from 'remark-gfm';
import { getDoc } from '../../api';
import PageLoader from '../PageLoader';
import { Callout, H2, H3, P, Rich } from './DocsPrimitives';
import { sectionForCorpus } from './sections';
import { useI18n } from '../../i18n';

// Corpus documents (docs/*.md and CHANGELOG.md, GET /api/docs/<id>) are the
// text the agents read with read_doc. They are rendered with this page's own
// headings and paragraphs, and without the chat's line breaks: the files are
// wrapped at 80 columns, a newline there is a space.
const CORPUS_COMPONENTS = {
  h1: ({ children }) => <H2>{children}</H2>,
  h2: ({ children }) => <H2>{children}</H2>,
  h3: ({ children }) => <H3>{children}</H3>,
  p: ({ children }) => <P>{children}</P>,
  ul: ({ children }) => <ul className="list-disc pl-5 my-2 space-y-2 text-sm text-gray-600 leading-relaxed">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal pl-5 my-2 space-y-2 text-sm text-gray-600 leading-relaxed">{children}</ol>,
  code: ({ children }) => <code className="px-1 py-0.5 rounded bg-gray-100 text-[12px] text-gray-800">{children}</code>,
  a: ({ href, children }) => {
    // Corpus files link each other as `instances.md#anchor`. Inside the app that
    // is the section showing that document, or plain text when none does.
    const corpus = /^([a-z0-9-]+)\.md(#.*)?$/.exec(href || '');
    if (corpus) {
      const section = sectionForCorpus(corpus[1]);
      return section
        ? <Link to={`/docs/${section}`} className="text-indigo-600 hover:underline">{children}</Link>
        : <span>{children}</span>;
    }
    return <a href={href} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline">{children}</a>;
  },
};

// The changelog is not written here: it is CHANGELOG.md from the corpus, so
// this page, read_doc and the site all show the same text. It is kept in English.
export function ChangelogDoc() {
  const { t } = useI18n();
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let live = true;
    getDoc('changelog')
      .then(({ data }) => { if (live) setDoc(data); })
      .catch((e) => { if (live) setError(e?.response?.data?.detail || e?.message || 'error'); });
    return () => { live = false; };
  }, []);
  if (error) return <Callout tone="warn">{t('docs.changelogError', { error })}</Callout>;
  if (!doc) {
    return (
      <PageLoader size="sm" label={t('docs.changelogLoading')} />
    );
  }
  return (
    <div>
      <P>{t('docs.changelogIntro')}</P>
      <Markdown remarkPlugins={[remarkGfm]} components={CORPUS_COMPONENTS}>{doc.content}</Markdown>
    </div>
  );
}

// "Full reference": a corpus document under a guide section, fetched the first
// time it is opened. The guide above it is translated; the reference is the
// text the agents answer from, in the interface language where docs/<lang>/
// has the page (the first hour ones) and in English otherwise. Keyed by the
// language where it is used, so a switch fetches the page again.
function CorpusRef({ id }) {
  const { t, language } = useI18n();
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState(null);
  const load = (e) => {
    if (!e.currentTarget.open || doc || error) return;
    getDoc(id, String(language || 'en').slice(0, 2))
      .then(({ data }) => setDoc(data))
      .catch((err) => setError(err?.response?.data?.detail || err?.message || 'error'));
  };
  return (
    <details onToggle={load} className="group border border-gray-200 rounded-xl bg-white my-2">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-gray-800 flex items-center justify-between">
        <span className="flex items-center gap-2"><BookOpen className="w-4 h-4 text-gray-400" />{t('docs.fullReference')}: <code className="font-normal text-gray-500">docs/{id}.md</code></span>
        <ArrowRight className="w-4 h-4 text-gray-300 group-open:rotate-90 transition-transform" />
      </summary>
      <div className="px-4 pb-4">
        {error && <Callout tone="warn">{t('docs.fullReferenceError', { error })}</Callout>}
        {!error && !doc && (
          <PageLoader size="sm" label={t('docs.fullReferenceLoading')} />
        )}
        {doc && <Markdown remarkPlugins={[remarkGfm]} components={CORPUS_COMPONENTS}>{doc.content}</Markdown>}
      </div>
    </details>
  );
}

// The full references under a section, refetched when the language changes.
export function CorpusRefs({ ids }) {
  const { language } = useI18n();
  return (
    <div className="mt-6">
      {ids.map((id) => <CorpusRef key={`${id}-${language}`} id={id} />)}
    </div>
  );
}

// A guide section written as data in the docsGuide namespace:
//   docsGuide.<k>.title / .lead / .s0 … .s5 ({ h, p?, points? }) / .callout?
// Subsections are read until the first missing one, so a section grows by
// adding keys in the three locale files, not by editing this component.
export function GuideDoc({ k, refs = [] }) {
  const { t } = useI18n();
  const opt = (key) => t(`docsGuide.${k}.${key}`, { defaultValue: '' });
  const parts = [];
  for (let i = 0; i < 10; i += 1) {
    const h = opt(`s${i}.h`);
    if (!h) break;
    const points = t(`docsGuide.${k}.s${i}.points`, { defaultValue: [] });
    parts.push({ h, p: opt(`s${i}.p`), points: Array.isArray(points) ? points : [] });
  }
  const callout = opt('callout');
  return (
    <div>
      <H2>{t(`docsGuide.${k}.title`)}</H2>
      <P><Rich>{t(`docsGuide.${k}.lead`)}</Rich></P>
      {parts.map((part, i) => (
        <div key={i}>
          <H3>{part.h}</H3>
          {part.p && <P><Rich>{part.p}</Rich></P>}
          {part.points.length > 0 && (
            <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
              {part.points.map((pt, j) => <li key={j}><Rich>{pt}</Rich></li>)}
            </ul>
          )}
        </div>
      ))}
      {callout && <Callout tone="warn"><Rich>{callout}</Rich></Callout>}
      {refs.length > 0 && (
        <CorpusRefs ids={refs} />
      )}
    </div>
  );
}


export function Faq() {
  const { t } = useI18n();
  // Each entry resolves to `docs.faq.<id>.q` / `.a`.
  const items = [
    'backendWontStart', 'frontendCantReach', 'runsFailImmediately', 'dockerCompose',
    'budgetExceeded', 'zeroCost', 'modelMissing', 'textNotChart', 'nothingStreams',
    'jobNeverFired', 'reopenOnboarding',
  ].map((id) => ({ q: t(`docs.faq.${id}.q`), a: t(`docs.faq.${id}.a`) }));
  return (
    <div>
      <H2>{t('docs.troubleshootingFaq')}</H2>
      <div className="space-y-2 my-3">
        {items.map((it, i) => (
          <details key={i} className="group border border-gray-200 rounded-xl bg-white">
            <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-gray-800 flex items-center justify-between">
              {it.q}
              <ArrowRight className="w-4 h-4 text-gray-300 group-open:rotate-90 transition-transform" />
            </summary>
            <div className="px-4 pb-3 text-sm text-gray-600">{it.a}</div>
          </details>
        ))}
      </div>
    </div>
  );
}

// Each renders a short explainer plus a live, interactive widget bound to the
// real backend (and the active workspace where relevant).
