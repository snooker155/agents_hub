// Artifacts: what the agents produced and work with, under one menu item.
//
// Two tabs over two existing pages. Views (the gallery of charts, graphs,
// scenes, decks and documents an agent built as answers, docs/views.md) and
// Files (the workspace's files by id, uploaded, saved by an agent or kept from
// a chat, docs/files.md) are the same class of object: a view's exports land
// in the workspace files, an html view is built from them, and a person
// looking for "the thing the agent made" should not have to know which of the
// two it became. Memory is deliberately not here: it is what the agents know
// about the user, with its own lifecycle, and lives under Tools.
//
// The tabs are routes (`/artifacts`, `/artifacts/files`), so a tab is a link
// one can share and the old `/views` and `/files` addresses redirect into
// them with their query intact (`/files?file=<id>` still opens that file).
// Each tab renders its page unchanged, header and all: the tab strip says
// where you are, the page keeps its own actions.
import { lazy, Suspense } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { FolderOpen, Images } from 'lucide-react';
import { useI18n } from '../i18n';
import PageLoader from '../components/PageLoader';

const Views = lazy(() => import('./Views'));
const Files = lazy(() => import('./Files'));

const ARTIFACT_TABS = [
  { key: 'views', path: '/artifacts', icon: Images },
  { key: 'files', path: '/artifacts/files', icon: FolderOpen },
];

export function ArtifactTabs({ active }) {
  const { t } = useI18n();
  return (
    <div role="tablist" aria-label={t('artifacts.title')}
      className="flex gap-1 px-4 sm:px-6 pt-3 border-b border-gray-100 dark:border-gray-800 shrink-0">
      {ARTIFACT_TABS.map(({ key, path, icon: Icon }) => (
        <Link key={key} to={path} role="tab" aria-selected={active === key} id={`artifacts-tab-${key}`}
          className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm -mb-px border-b-2 ${
            active === key
              ? 'border-indigo-600 text-indigo-700 dark:text-indigo-300 font-medium'
              : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-200'}`}>
          <Icon className="w-4 h-4" /> {t(`artifacts.tabs.${key}`)}
        </Link>
      ))}
    </div>
  );
}

export default function Artifacts({ tab = 'views' }) {
  const { t } = useI18n();
  return (
    <div className="h-full min-h-0 flex flex-col">
      <ArtifactTabs active={tab} />
      <div className="flex-1 min-h-0" role="tabpanel" aria-labelledby={`artifacts-tab-${tab}`}>
        <Suspense fallback={<PageLoader label={t('artifacts.loading')} />}>
          {tab === 'files' ? <Files /> : <Views />}
        </Suspense>
      </div>
    </div>
  );
}

/** `/views` and `/files` moved under Artifacts; the query (``?file=<id>``) comes along. */
export function ArtifactsRedirect({ tab = 'views' }) {
  const { search, hash } = useLocation();
  const target = ARTIFACT_TABS.find((x) => x.key === tab) || ARTIFACT_TABS[0];
  return <Navigate to={{ pathname: target.path, search, hash }} replace />;
}
