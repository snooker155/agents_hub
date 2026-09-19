#!/usr/bin/env node
/**
 * Copies the shipped documentation corpus (../docs/*.md) into site/guide/,
 * where VitePress can build it.
 *
 * The corpus is not edited here and is not a VitePress directory: tests assert
 * that docs/*.md matches docs/index.json exactly (tests/test_docs_corpus.py),
 * so a .vitepress folder or an extra index.md next to it would break the build
 * of the product itself. Hence: copy out, never write back.
 *
 * Each page gets frontmatter derived from the index, which is what fills the
 * <title> and the meta description of the published page.
 *
 * Run:  npm run sync   (also runs automatically before dev and build)
 */
import { mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE = join(dirname(fileURLToPath(import.meta.url)), '..');
const CORPUS = join(SITE, '..', 'docs');
const OUT = join(SITE, 'guide');

/** Markdown to plain text, for a meta description that is not full of syntax. */
function plain(markdown) {
  return markdown
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')   // [text](link) -> text
    .replace(/[`*_]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

const index = JSON.parse(readFileSync(join(CORPUS, 'index.json'), 'utf8')).docs || [];
const onDisk = new Set(
  readdirSync(CORPUS).filter((f) => f.endsWith('.md')).map((f) => f.slice(0, -3)),
);
const indexed = new Set(index.map((e) => e.id));

const missing = [...indexed].filter((id) => !onDisk.has(id));
const unlisted = [...onDisk].filter((id) => !indexed.has(id));
if (missing.length || unlisted.length) {
  console.error(
    `docs/index.json and docs/*.md disagree — only in index: ${missing.join(', ') || 'none'}; ` +
    `only on disk: ${unlisted.join(', ') || 'none'}`,
  );
  process.exit(1);
}

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

for (const entry of index) {
  const body = readFileSync(join(CORPUS, `${entry.id}.md`), 'utf8');
  const frontmatter = [
    '---',
    `title: ${JSON.stringify(entry.title)}`,
    `description: ${JSON.stringify(plain(entry.summary || '').slice(0, 300))}`,
    '---',
    '',
    '<!-- Generated from docs/ by scripts/sync-docs.mjs. Edit the corpus, not this copy. -->',
    '',
  ].join('\n');
  writeFileSync(join(OUT, `${entry.id}.md`), frontmatter + body);
}

console.log(`synced ${index.length} documents into site/guide/`);
