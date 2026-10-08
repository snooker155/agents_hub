import { BookOpen } from 'lucide-react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { GROUPS, SECTIONS } from '../components/docs/sections';
import { useI18n } from '../i18n';

// ---------------------------------------------------------------------------
// Docs — an in-app documentation hub with its own left-hand section nav.
// Content is authored as JSX (rather than markdown files) so interactive
// widgets can be embedded directly inside the prose. Those widgets are
// synthetic by design (see components/docs/ExampleWidgets) — they teach a
// feature without depending on, or exposing, the reader's live instance. The
// one live element is OnboardingChecklist, which probes the running backend.
//   /docs                  -> Getting Started
//   /docs/:section         -> a specific section
// ---------------------------------------------------------------------------

export default function Docs() {
  const { t } = useI18n();
  const { section } = useParams();
  const navigate = useNavigate();
  const active = SECTIONS.find((s) => s.id === section) || SECTIONS[0];
  const Content = active.render;

  return (
    <PageContainer fill>
      {/* Every other page opens with the same heading row; this one used to
          start straight on its section nav, so the reader had no title telling
          them where they were. */}
      <PageHeader
        icon={BookOpen}
        title={t('docs.documentation')}
        description={t('docs.pageDescription')}
      />

      <div className="flex min-h-0 flex-1 gap-6">
      {/* In-page section nav */}
      <nav className="w-56 shrink-0 hidden md:block overflow-y-auto">
        <div className="pb-8">
          {GROUPS.map((group) => (
            <div key={group.key} className="mb-2">
              <p className="px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
                {t(`docs.nav.groups.${group.key}`)}
              </p>
              {group.items.map((s) => {
                const Icon = s.icon;
                const isActive = s.id === active.id;
                return (
                  <Link
                    key={s.id}
                    to={`/docs/${s.id}`}
                    className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
                      isActive
                        ? 'bg-indigo-50 text-indigo-600 font-semibold'
                        : 'text-gray-600 hover:bg-gray-100'
                    }`}
                  >
                    <Icon className="w-4 h-4 shrink-0" />
                    {t(s.navKey || `docs.nav.${s.key}`)}
                  </Link>
                );
              })}
            </div>
          ))}
        </div>
      </nav>

      {/* Content */}
      <div className="flex-1 min-w-0 overflow-y-auto">
        <div className="max-w-3xl pb-16">
          {/* Mobile section selector */}
          <div className="md:hidden mb-4">
            <select
              value={active.id}
              onChange={(e) => navigate(`/docs/${e.target.value}`)}
              className="w-full bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm"
            >
              {GROUPS.map((g) => (
                <optgroup key={g.key} label={t(`docs.nav.groups.${g.key}`)}>
                  {g.items.map((s) => (
                    <option key={s.id} value={s.id}>{t(s.navKey || `docs.nav.${s.key}`)}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>
          <Content />
        </div>
      </div>
      </div>
    </PageContainer>
  );
}

