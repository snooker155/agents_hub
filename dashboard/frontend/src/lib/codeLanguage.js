/**
 * The highlighter's language name for a file, from its extension
 * (lib/highlight.js knows some of them; the rest still get a labelled code
 * block, in plain text). Empty for a file that is not code.
 */
const BY_EXTENSION = {
  py: 'python', pyi: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'jsx',
  ts: 'typescript', tsx: 'tsx', json: 'json', jsonl: 'json', yaml: 'yaml', yml: 'yaml',
  sh: 'bash', bash: 'bash', zsh: 'bash', env: 'bash', css: 'css', scss: 'css', sql: 'sql',
  go: 'go', rs: 'rust', java: 'java', kt: 'kotlin', c: 'c', h: 'c', cc: 'cpp', cpp: 'cpp', hpp: 'cpp',
  cs: 'csharp', rb: 'ruby', php: 'php', swift: 'swift', xml: 'xml', toml: 'toml', ini: 'ini',
  dockerfile: 'dockerfile', vue: 'vue', svelte: 'svelte', lua: 'lua', r: 'r', pl: 'perl', ps1: 'powershell',
  html: 'html', htm: 'html', md: 'markdown', markdown: 'markdown',
};

export function codeLanguageFor(name) {
  const base = String(name || '').split('/').pop();
  if (base === 'Dockerfile' || base === 'Makefile') return 'bash';
  const ext = base.toLowerCase().split('.').pop();
  return BY_EXTENSION[ext] || '';
}

// Files the Run of a workspace file takes, by extension (views/file_run.py
// RUN_LANG_BY_EXT): an ES module (.mjs) cannot run under the launcher.
const RUNNABLE_FILE_EXTENSIONS = new Set(['py', 'js', 'cjs', 'sh', 'bash']);

export function isRunnableFile(name) {
  const base = String(name || '').split('/').pop().toLowerCase();
  return base.includes('.') && RUNNABLE_FILE_EXTENSIONS.has(base.split('.').pop());
}
