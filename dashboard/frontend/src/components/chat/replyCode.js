/**
 * The fenced code blocks in a conversation's replies, as the Code panel lists
 * them under "From replies": read off the messages, never stored. One becomes
 * a code view (a snippet with versions, runs and a place in a project) only
 * when the person saves it as one.
 *
 * A block is `{ id, message_id, run_id, index, language, filename, name, body }`;
 * `filename` is a token with a dot in the fence's info string ("```python
 * main.py"), when the reply gave one, and `name` is what the block is called
 * either way: that filename, else one made from the code (a file named in a
 * leading comment, the first class or function), else from the prompt the
 * reply answers, with the language's extension. Two blocks that would share
 * a name are numbered, since the panel groups snippets by filename.
 */
const FENCE = /^ {0,3}```([^\n]*)\n([\s\S]*?)\n? {0,3}```[ \t]*$/gm;

const EXTENSIONS = {
  python: '.py', py: '.py', javascript: '.js', js: '.js', node: '.js', jsx: '.jsx', typescript: '.ts',
  ts: '.ts', tsx: '.tsx', bash: '.sh', sh: '.sh', shell: '.sh', zsh: '.sh', json: '.json', sql: '.sql',
  html: '.html', css: '.css', scss: '.scss', yaml: '.yaml', yml: '.yaml', toml: '.toml', xml: '.xml',
  ini: '.ini', markdown: '.md', md: '.md', go: '.go', rust: '.rs', rs: '.rs', java: '.java', kotlin: '.kt',
  swift: '.swift', c: '.c', cpp: '.cpp', 'c++': '.cpp', csharp: '.cs', cs: '.cs', ruby: '.rb', rb: '.rb',
  php: '.php', lua: '.lua', r: '.R', perl: '.pl', dart: '.dart', scala: '.scala', powershell: '.ps1',
  dockerfile: '.dockerfile', text: '.txt', txt: '.txt', plaintext: '.txt',
};

// Languages whose files are conventionally snake_case; the rest get kebab-case.
const SNAKE = new Set(['python', 'py', 'rust', 'rs', 'ruby', 'rb', 'go', 'c', 'cpp', 'c++', 'lua', 'perl', 'r']);

// Words that say what was asked for rather than what about, in the prompt
// languages this dashboard speaks (see i18n/locales). Left out of a name.
const STOP = new Set(('a an the to for of in on with and or is are be this that it me my please write make create ' +
  'add build give show code script function program example how can you could would should want need fix using use now then also again ' +
  'напиши сделай создай добавь дай покажи исправь пожалуйста мне мой моя как можно нужно надо хочу что чтобы который ' +
  'которая которое которые это этот эта и или в на с для по из от у к не да код скрипт функцию функция программу программа ' +
  'пример при через используя теперь потом также тоже ещё еще снова ' +
  'schreibe schreib mache mach erstelle erstell bitte mir mein meine wie kann könnte sollte will brauche der die das ' +
  'ein eine einen und oder in auf mit für von zu aus bei nicht code skript funktion programm beispiel jetzt dann auch nochmal').split(/\s+/));

const extensionFor = (language) => EXTENSIONS[language] || (/^[a-z0-9]{1,6}$/.test(language) ? `.${language}` : '.txt');

const slug = (text, sep) => String(text || '')
  .replace(/([a-z0-9])([A-Z])/g, `$1${sep}$2`)
  .toLowerCase()
  .replace(/[^\p{L}\p{N}]+/gu, sep)
  .replace(new RegExp(`^\\${sep}+|\\${sep}+$`, 'g'), '')
  .slice(0, 48)
  .replace(new RegExp(`\\${sep}+$`), '');

// A file named in a leading comment: "# main.py", "// src/app.js", "<!-- index.html -->".
const HEADER_FILE = /^\s*(?:#|\/\/|--|;|\/\*|<!--|%|')\s*(?:file(?:name)?\s*:\s*)?([\w./-]*\/)?([\w-]+\.[a-z0-9]{1,8})\b/i;

// The first top-level class or function, whichever kind comes first in the
// language's habit of naming files: a class over a function.
const DEFINITIONS = [
  /^(?:export\s+(?:default\s+)?)?(?:pub(?:\([^)]*\))?\s+)?(?:abstract\s+|final\s+|data\s+|public\s+|internal\s+)?(?:class|struct|enum|interface|trait|impl)\s+([A-Za-z_]\w*)/m,
  /^(?:export\s+(?:default\s+)?)?(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:def|fn|func|function)\b\s*\*?\s*(?:\([^)]*\)\s*)?([A-Za-z_]\w*)/m,
  /^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$]\w*)\s*=\s*(?:async\s*)?(?:\(|function\b|[\w$]+\s*=>)/m,
  /^\s*create\s+(?:or\s+replace\s+)?(?:temp(?:orary)?\s+)?(?:table|view|function|procedure|index)\s+(?:if\s+not\s+exists\s+)?["`]?([A-Za-z_]\w*)/im,
  /<title>\s*([^<]{1,60}?)\s*<\/title>/i,
  /^package\s+([A-Za-z_]\w*)/m,
];

function nameFromCode(body) {
  const head = body.split('\n').slice(0, 3);
  for (const line of head) {
    const m = line.match(HEADER_FILE);
    if (m) return { stem: m[2].replace(/\.[a-z0-9]+$/i, ''), ext: m[2].match(/\.[a-z0-9]+$/i)[0] };
  }
  for (const re of DEFINITIONS) {
    const m = body.match(re);
    if (m && m[1]) return { stem: m[1] };
  }
  return null;
}

function nameFromPrompt(prompt) {
  const text = String(prompt || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`[^`]*`/g, ' ')
    .replace(/https?:\/\/\S+/g, ' ');
  const words = text.toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
  const kept = words.filter((w) => w.length > 1 && !STOP.has(w)).slice(0, 4);
  return kept.length ? kept.join(' ') : '';
}

/** The name a block goes by: see the file comment. `prompt` is the message it answers. */
export function suggestBlockName(block, prompt) {
  if (block.filename) return block.filename;
  const sep = SNAKE.has(block.language) ? '_' : '-';
  const fromCode = nameFromCode(block.body);
  if (fromCode) {
    const stem = slug(fromCode.stem, sep);
    if (stem) return `${stem}${fromCode.ext || extensionFor(block.language)}`;
  }
  const stem = slug(nameFromPrompt(prompt), sep);
  return `${stem || 'snippet'}${extensionFor(block.language)}`;
}

export function replyCodeBlocks(messages) {
  const out = [];
  const used = new Map();
  let prompt = '';
  for (const m of messages || []) {
    if (m?.role === 'user' && !m.steer && typeof m.content === 'string') prompt = m.content;
    if (m?.role !== 'agent' || typeof m.content !== 'string' || !m.content.includes('```')) continue;
    let index = 0;
    for (const match of m.content.matchAll(FENCE)) {
      const info = String(match[1] || '').trim().split(/\s+/).filter(Boolean);
      const body = match[2] || '';
      if (!body.trim()) continue;
      const language = (info[0] && !info[0].includes('.') ? info[0] : '').toLowerCase();
      const filename = info.find((tok) => tok.includes('.') && !tok.includes('=')) || '';
      const block = {
        id: `reply:${m.id || m.run_id || 'msg'}:${index}`,
        message_id: m.id || null,
        run_id: m.run_id || null,
        index,
        language,
        filename,
        body,
      };
      const wanted = suggestBlockName(block, prompt);
      const seen = used.get(wanted) || 0;
      used.set(wanted, seen + 1);
      block.name = seen ? wanted.replace(/(\.[^.]+)?$/, `-${seen + 1}$1`) : wanted;
      out.push(block);
      index += 1;
    }
  }
  return out;
}

/** What the list calls a block: its name, else its language and first line. */
export function replyBlockLabel(block, fallback = 'text') {
  if (block.name || block.filename) return block.name || block.filename;
  const first = (block.body.split('\n').find((l) => l.trim()) || '').trim();
  return `${block.language || fallback} · ${first.slice(0, 40)}`;
}
