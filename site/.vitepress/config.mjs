import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitepress';

const HERE = dirname(fileURLToPath(import.meta.url));
const CORPUS = JSON.parse(
  readFileSync(join(HERE, '..', '..', 'docs', 'index.json'), 'utf8'),
).docs || [];

/** id -> title, so the sidebar never drifts from what the corpus actually says. */
const TITLES = new Map(CORPUS.map((e) => [e.id, e.title]));

/**
 * The reading order. Ids come from docs/index.json; anything the corpus gains
 * later and this list does not mention lands in a final "More" group rather
 * than disappearing from the site.
 */
const GROUPS = [
  ['Start here', ['overview', 'installation', 'cli', 'troubleshooting']],
  ['The work', ['workspaces', 'projects', 'tasks', 'outcomes', 'scheduling', 'deployments']],
  ['Agents', ['agents', 'agent-loop', 'tool-policy', 'guardrails', 'system-agents', 'imported-agents', 'tools-and-capabilities', 'skills', 'memory']],
  ['Talking to them', ['chat', 'page-chat', 'steering', 'telegram']],
  ['More than one agent', ['flows', 'loops', 'teams', 'nodes', 'instances']],
  ['What they produce', ['views', 'playground']],
  ['Measurement', ['evals', 'costs', 'web-logs', 'sessions-and-runs']],
  ['Running the service', ['settings', 'models', 'marketplace', 'containers', 'environments', 'service-health']],
];

const placed = new Set(GROUPS.flatMap(([, ids]) => ids));
const leftovers = CORPUS.map((e) => e.id).filter((id) => !placed.has(id));

const item = (id) => ({ text: TITLES.get(id) || id, link: `/guide/${id}` });

const sidebar = [
  ...GROUPS.map(([text, ids]) => ({
    text,
    collapsed: false,
    items: ids.filter((id) => TITLES.has(id)).map(item),
  })),
  ...(leftovers.length ? [{ text: 'More', collapsed: false, items: leftovers.map(item) }] : []),
];

/**
 * The recipes sidebar, read from site/recipes/*.md at config time: the title
 * is each file's first `# ` line (or its frontmatter title), the order is index first and then by title.
 * A missing or empty folder gives an empty sidebar rather than a failed build.
 */
function recipesSidebar() {
  const dir = join(HERE, '..', 'recipes');
  if (!existsSync(dir)) return [];
  const entries = readdirSync(dir)
    .filter((f) => f.endsWith('.md'))
    .map((f) => {
      const id = f.replace(/\.md$/, '');
      const src = readFileSync(join(dir, f), 'utf8');
      // The first `# ` heading, else the frontmatter title, else the file name.
      const body = src.replace(/^---\n[\s\S]*?\n---\n/, '');
      const heading = body.match(/^# (.+)$/m);
      const front = src.match(/^---\n[\s\S]*?^title:\s*["']?(.+?)["']?\s*$/m);
      return { id, text: (heading?.[1] || front?.[1] || id).trim() };
    });
  const index = entries.find((e) => e.id === 'index');
  const rest = entries.filter((e) => e.id !== 'index').sort((a, b) => a.text.localeCompare(b.text));
  return [{
    text: 'Recipes',
    collapsed: false,
    items: [
      ...(index ? [{ text: index.text, link: '/recipes/' }] : []),
      ...rest.map((e) => ({ text: e.text, link: `/recipes/${e.id}` })),
    ],
  }];
}

export default defineConfig({
  // Published at https://snooker155.github.io/agents_hub/ — every asset and
  // link is resolved against this prefix, so it must match the repository name.
  base: '/agents_hub/',
  lang: 'en-US',
  title: 'Agents Hub',
  description:
    'A local multi-agent development environment: define AI agents, give them tools and memory, and run them against real work in a workspace.',
  cleanUrls: false,     // GitHub Pages serves the .html files this emits.
  lastUpdated: true,
  metaChunk: true,
  // /demo/ is a separate app copied into public/ by scripts/build-demo.mjs,
  // not a page, and is absent from a docs only build.
  ignoreDeadLinks: [/^\/demo\//],

  head: [
    ['link', { rel: 'icon', href: '/agents_hub/favicon.svg', type: 'image/svg+xml' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:title', content: 'Agents Hub' }],
    ['meta', {
      property: 'og:description',
      content: 'Define AI agents, give them tools and memory, and run them, alone or in groups, against real work.',
    }],
    ['meta', { name: 'theme-color', content: '#3f66d8' }],
  ],

  markdown: {
    // The corpus fences .env samples as ```env, which Shiki does not know.
    languageAlias: { env: 'ini' },
  },

  themeConfig: {
    siteTitle: 'Agents Hub',
    logo: '/logo.svg',

    nav: [
      { text: 'Documentation', link: '/guide/overview', activeMatch: '/guide/' },
      { text: 'Install', link: '/guide/installation' },
      { text: 'Recipes', link: '/recipes/', activeMatch: '/recipes/' },
      // A separate app (the dashboard over recorded data), not a VitePress
      // page: `target` stops the VitePress router from trying to render it.
      { text: 'Demo', link: '/demo/index.html', target: '_self' },
      { text: 'GitHub', link: 'https://github.com/snooker155/agents_hub' },
    ],

    sidebar: { '/guide/': sidebar, '/recipes/': recipesSidebar() },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/snooker155/agents_hub' },
    ],

    search: {
      // Local index, built at compile time: no third-party service, nothing
      // for the published page to call out to.
      provider: 'local',
    },

    outline: { level: [2, 3], label: 'On this page' },

    editLink: {
      // Pages under /guide/ are copies; the file a reader should edit is the
      // corpus entry they were made from.
      pattern: ({ filePath }) =>
        `https://github.com/snooker155/agents_hub/edit/main/${filePath.replace(/^guide\//, 'docs/')}`,
      text: 'Edit this page on GitHub',
    },

    docFooter: { prev: 'Previous', next: 'Next' },

    footer: {
      message:
        'These pages are the corpus the product ships in <code>docs/</code>, published as read.',
      copyright: 'Agents Hub',
    },
  },

  sitemap: { hostname: 'https://snooker155.github.io/agents_hub/' },
});
