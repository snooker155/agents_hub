/**
 * Agents Hub chat widget (docs/widget.md).
 *
 * One tag on any site:
 *
 *   <script src="https://<hub>/widget.js" data-widget="wgt_..." data-key="ahw_..." async></script>
 *
 * draws a chat bubble that talks to one agent of one workspace through the
 * hub's public widget API (/api/widgets/public/<id>/..., see
 * dashboard/backend/routes/widget.py). Plain JavaScript on purpose: no
 * framework and no build step, so the hub serves this file as it is and the
 * host page pays for nothing it does not use.
 *
 * Isolation. Everything lives in a closed Shadow DOM: the host page's CSS
 * cannot reach in, and the widget's CSS cannot leak out. `:host { all:
 * initial }` also stops inherited properties (fonts, colours, line height)
 * from crossing the boundary.
 *
 * Safety. No untrusted text ever goes through innerHTML. Replies are drawn
 * by a tiny renderer (paragraphs, lists, code blocks, inline code, bold,
 * http(s) links, [n] source markers) that only ever creates elements and
 * text nodes. Links open in a new tab with rel="noopener noreferrer".
 *
 * Attributes on the script tag: data-widget (required), data-key (the
 * publishable key, required), data-hub (the hub's origin, default: this
 * script's own origin), data-preview (a preview ticket, set by the hub's
 * Widgets page) and data-open ("true" opens the panel at once).
 *
 * The page can drive it too: window.AgentsHubWidget.open(), .close(),
 * .toggle(), each optionally with a widget id.
 */
(() => {
  'use strict';

  // ── strings ─────────────────────────────────────────────────────────────────

  const STRINGS = {
    en: {
      open: 'Open chat',
      close: 'Close chat',
      title: 'Chat',
      threads: 'Conversations',
      showThreads: 'Show conversations',
      back: 'Back to the conversation',
      newThread: 'New conversation',
      untitled: 'New conversation',
      deleteThread: 'Delete conversation {title}',
      confirmDelete: 'Delete this conversation?',
      noThreads: 'No conversations yet.',
      placeholder: 'Write a message',
      send: 'Send',
      stop: 'Stop',
      attach: 'Attach files',
      removeFile: 'Remove {name}',
      sources: 'Sources',
      handoff: 'Handed over to {name}',
      usingTool: 'Using {tool}',
      working: 'Working on it',
      stopped: 'Stopped.',
      you: 'You',
      assistant: 'Assistant',
      messages: 'Messages',
      message: 'Message',
      preview: 'Preview',
      errors: {
        failed: 'The assistant could not answer. Please try again.',
        rate_limited: 'Too many messages. Please wait a moment.',
        daily_limit: 'This chat has reached its limit for today.',
        attachment_too_large: '{name} is too large. The limit is {size}.',
        attachments_too_large: 'The files are too large together.',
        too_many_attachments: 'At most {count} files per message.',
        attachments_disabled: 'Files cannot be attached here.',
        message_too_long: 'The message is too long.',
        busy: 'Still answering the previous message.',
        too_many_threads: 'Too many conversations. Please delete an old one.',
        network: 'Connection problem. Please try again.',
        unavailable: 'The chat is not available right now.',
        generic: 'Something went wrong. Please try again.',
      },
    },
    ru: {
      open: 'Открыть чат',
      close: 'Закрыть чат',
      title: 'Чат',
      threads: 'Разговоры',
      showThreads: 'Показать разговоры',
      back: 'Вернуться к разговору',
      newThread: 'Новый разговор',
      untitled: 'Новый разговор',
      deleteThread: 'Удалить разговор {title}',
      confirmDelete: 'Удалить этот разговор?',
      noThreads: 'Разговоров пока нет.',
      placeholder: 'Напишите сообщение',
      send: 'Отправить',
      stop: 'Остановить',
      attach: 'Прикрепить файлы',
      removeFile: 'Убрать {name}',
      sources: 'Источники',
      handoff: 'Разговор передан: {name}',
      usingTool: 'Использует {tool}',
      working: 'Работаю над ответом',
      stopped: 'Остановлено.',
      you: 'Вы',
      assistant: 'Ассистент',
      messages: 'Сообщения',
      message: 'Сообщение',
      preview: 'Предпросмотр',
      errors: {
        failed: 'Ассистент не смог ответить. Попробуйте ещё раз.',
        rate_limited: 'Слишком много сообщений. Подождите немного.',
        daily_limit: 'Чат исчерпал лимит на сегодня.',
        attachment_too_large: 'Файл {name} слишком большой. Предел: {size}.',
        attachments_too_large: 'Файлы вместе слишком большие.',
        too_many_attachments: 'Не больше {count} файлов в одном сообщении.',
        attachments_disabled: 'Здесь нельзя прикреплять файлы.',
        message_too_long: 'Сообщение слишком длинное.',
        busy: 'Ассистент ещё отвечает на предыдущее сообщение.',
        too_many_threads: 'Слишком много разговоров. Удалите какой-нибудь старый.',
        network: 'Проблема со связью. Попробуйте ещё раз.',
        unavailable: 'Чат сейчас недоступен.',
        generic: 'Что-то пошло не так. Попробуйте ещё раз.',
      },
    },
    de: {
      open: 'Chat öffnen',
      close: 'Chat schließen',
      title: 'Chat',
      threads: 'Unterhaltungen',
      showThreads: 'Unterhaltungen anzeigen',
      back: 'Zurück zur Unterhaltung',
      newThread: 'Neue Unterhaltung',
      untitled: 'Neue Unterhaltung',
      deleteThread: 'Unterhaltung {title} löschen',
      confirmDelete: 'Diese Unterhaltung löschen?',
      noThreads: 'Noch keine Unterhaltungen.',
      placeholder: 'Nachricht schreiben',
      send: 'Senden',
      stop: 'Stoppen',
      attach: 'Dateien anhängen',
      removeFile: '{name} entfernen',
      sources: 'Quellen',
      handoff: 'Übergeben an {name}',
      usingTool: 'Verwendet {tool}',
      working: 'Arbeite an der Antwort',
      stopped: 'Gestoppt.',
      you: 'Sie',
      assistant: 'Assistent',
      messages: 'Nachrichten',
      message: 'Nachricht',
      preview: 'Vorschau',
      errors: {
        failed: 'Der Assistent konnte nicht antworten. Bitte versuchen Sie es erneut.',
        rate_limited: 'Zu viele Nachrichten. Bitte warten Sie einen Moment.',
        daily_limit: 'Dieser Chat hat sein Tageslimit erreicht.',
        attachment_too_large: '{name} ist zu groß. Die Grenze liegt bei {size}.',
        attachments_too_large: 'Die Dateien sind zusammen zu groß.',
        too_many_attachments: 'Höchstens {count} Dateien pro Nachricht.',
        attachments_disabled: 'Hier können keine Dateien angehängt werden.',
        message_too_long: 'Die Nachricht ist zu lang.',
        busy: 'Die vorherige Nachricht wird noch beantwortet.',
        too_many_threads: 'Zu viele Unterhaltungen. Bitte löschen Sie eine alte.',
        network: 'Verbindungsproblem. Bitte versuchen Sie es erneut.',
        unavailable: 'Der Chat ist gerade nicht verfügbar.',
        generic: 'Etwas ist schiefgelaufen. Bitte versuchen Sie es erneut.',
      },
    },
  };

  const LANGS = Object.keys(STRINGS);

  /** The widget's language: its own setting, else the first of the
   *  browser's languages the widget carries, else English. */
  function pickLanguage(configured, browserLanguages) {
    if (configured && configured !== 'auto' && STRINGS[configured]) return configured;
    for (const tag of browserLanguages || []) {
      const base = String(tag || '').toLowerCase().split('-')[0];
      if (STRINGS[base]) return base;
    }
    return 'en';
  }

  function translate(lang, key, vars) {
    const lookup = (dict) => key.split('.').reduce((node, part) => (node == null ? node : node[part]), dict);
    let value = lookup(STRINGS[lang] || STRINGS.en);
    if (typeof value !== 'string') value = lookup(STRINGS.en);
    if (typeof value !== 'string') return key;
    return value.replace(/\{(\w+)\}/g, (match, name) => (
      vars && Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match));
  }

  function formatBytes(bytes) {
    const n = Number(bytes) || 0;
    if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(n % (1024 * 1024) ? 1 : 0)} MB`;
    if (n >= 1024) return `${Math.round(n / 1024)} KB`;
    return `${n} B`;
  }

  // ── the safe renderer ───────────────────────────────────────────────────────

  /** True for an absolute http(s) URL, the only links the widget draws. */
  function isSafeUrl(value) {
    try {
      const url = new URL(String(value));
      return url.protocol === 'https:' || url.protocol === 'http:';
    } catch {
      return false;
    }
  }

  // `code`, **bold**, [text](http...), a bare http(s) URL, a [n] source marker.
  const INLINE = /(`[^`\n]+`)|(\*\*(?=\S)[^*\n]*?\S\*\*)|(\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\))|(https?:\/\/[^\s<>()"']*[^\s<>()"'.,;:!?])|(\[(\d{1,3})\])/g;

  /** Inline markup of one line as nodes: text nodes and a few element kinds,
   *  never parsed HTML. */
  function renderInline(text, doc) {
    const out = [];
    const source = String(text || '');
    let last = 0;
    INLINE.lastIndex = 0;
    let match;
    while ((match = INLINE.exec(source)) !== null) {
      if (match.index > last) out.push(doc.createTextNode(source.slice(last, match.index)));
      if (match[1]) {
        const code = doc.createElement('code');
        code.textContent = match[1].slice(1, -1);
        out.push(code);
      } else if (match[2]) {
        const strong = doc.createElement('strong');
        strong.textContent = match[2].slice(2, -2);
        out.push(strong);
      } else if (match[3]) {
        out.push(isSafeUrl(match[5]) ? link(doc, match[5], match[4]) : doc.createTextNode(match[0]));
      } else if (match[6]) {
        out.push(isSafeUrl(match[6]) ? link(doc, match[6], match[6]) : doc.createTextNode(match[6]));
      } else if (match[7]) {
        const cite = doc.createElement('sup');
        cite.className = 'cite';
        cite.textContent = `[${match[8]}]`;
        out.push(cite);
      }
      last = INLINE.lastIndex;
    }
    if (last < source.length) out.push(doc.createTextNode(source.slice(last)));
    return out;
  }

  function link(doc, href, label) {
    const a = doc.createElement('a');
    a.href = href;
    a.textContent = label;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    return a;
  }

  /** A reply as a DocumentFragment: fenced code blocks, bullet and numbered
   *  lists, paragraphs (a single newline is a line break), inline markup. */
  function renderMarkdown(text, doc = document) {
    const frag = doc.createDocumentFragment();
    const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
    let para = null;
    let list = null;
    const closeBlocks = () => { para = null; list = null; };
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const fence = line.match(/^\s*```/);
      if (fence) {
        closeBlocks();
        const body = [];
        i += 1;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) {
          body.push(lines[i]);
          i += 1;
        }
        const pre = doc.createElement('pre');
        const code = doc.createElement('code');
        code.textContent = body.join('\n');
        pre.appendChild(code);
        frag.appendChild(pre);
        continue;
      }
      if (!line.trim()) {
        closeBlocks();
        continue;
      }
      const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
      const numbered = bullet ? null : line.match(/^\s*\d{1,3}[.)]\s+(.*)$/);
      if (bullet || numbered) {
        const tag = bullet ? 'ul' : 'ol';
        if (!list || list.tagName.toLowerCase() !== tag) {
          para = null;
          list = doc.createElement(tag);
          frag.appendChild(list);
        }
        const li = doc.createElement('li');
        renderInline((bullet || numbered)[1], doc).forEach((node) => li.appendChild(node));
        list.appendChild(li);
        continue;
      }
      list = null;
      if (!para) {
        para = doc.createElement('p');
        frag.appendChild(para);
      } else {
        para.appendChild(doc.createElement('br'));
      }
      renderInline(line, doc).forEach((node) => para.appendChild(node));
    }
    return frag;
  }

  // ── the SSE parser ──────────────────────────────────────────────────────────

  /** A streaming parser for text/event-stream: feed it decoded chunks as
   *  they arrive, cut anywhere; it calls onEvent with each `data:` payload
   *  parsed as JSON. Comments (`: ping`) and other fields are skipped. */
  function createSSEParser(onEvent) {
    let buffer = '';
    let data = [];
    const dispatch = () => {
      if (!data.length) return;
      const payload = data.join('\n');
      data = [];
      let parsed;
      try {
        parsed = JSON.parse(payload);
      } catch {
        return;
      }
      onEvent(parsed);
    };
    const handleLine = (line) => {
      if (line === '') {
        dispatch();
        return;
      }
      if (line[0] === ':') return;
      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? '' : line.slice(colon + 1);
      if (value[0] === ' ') value = value.slice(1);
      if (field === 'data') data.push(value);
    };
    return {
      push(chunk) {
        buffer += chunk;
        for (;;) {
          const cr = buffer.indexOf('\r');
          const lf = buffer.indexOf('\n');
          let end;
          if (cr !== -1 && (lf === -1 || cr < lf)) end = cr;
          else end = lf;
          if (end === -1) break;
          // A \r at the very end may be the first half of \r\n: wait for more.
          if (buffer[end] === '\r' && end === buffer.length - 1) break;
          const width = buffer[end] === '\r' && buffer[end + 1] === '\n' ? 2 : 1;
          const line = buffer.slice(0, end);
          buffer = buffer.slice(end + width);
          handleLine(line);
        }
      },
      end() {
        if (buffer) handleLine(buffer.replace(/\r$/, ''));
        buffer = '';
        dispatch();
      },
    };
  }

  // ── look ────────────────────────────────────────────────────────────────────

  // Named accents, each [light, dark]. A widget stores the name, never a
  // colour: nothing a site owner types ends up in this style sheet.
  const ACCENTS = {
    navy: ['#2a4fbd', '#8fb2ff'],
    blue: ['#1f63d6', '#7fb2ff'],
    teal: ['#0d7377', '#5fcfcb'],
    green: ['#1e7a3c', '#72d394'],
    amber: ['#9a5700', '#f3b04f'],
    rose: ['#b8245a', '#ff90b5'],
    slate: ['#3e4a5e', '#b3bfd3'],
  };

  function styles(accent) {
    const [light, dark] = ACCENTS[accent] || ACCENTS.navy;
    return `
:host { all: initial; }
.root {
  --ah-accent: ${light}; --ah-on-accent: #ffffff;
  --ah-bg: #ffffff; --ah-surface: #f3f5f9; --ah-border: #dbe1ea;
  --ah-text: #182032; --ah-muted: #5a6477; --ah-danger: #b42318; --ah-shadow: rgba(16, 24, 40, 0.18);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 14px; line-height: 1.45; color: var(--ah-text);
}
@media (prefers-color-scheme: dark) {
  .root {
    --ah-accent: ${dark}; --ah-on-accent: #0b1020;
    --ah-bg: #151b27; --ah-surface: #1e2636; --ah-border: #2d374b;
    --ah-text: #e6ebf5; --ah-muted: #9ba7bc; --ah-danger: #ff8a80; --ah-shadow: rgba(0, 0, 0, 0.5);
  }
}
*, *::before, *::after { box-sizing: border-box; }
/* Every rule below that sets display would otherwise beat the hidden attribute. */
[hidden] { display: none !important; }
button, textarea, input { font: inherit; color: inherit; }
button { cursor: pointer; }
:focus-visible { outline: 2px solid var(--ah-accent); outline-offset: 2px; }
.launcher {
  position: fixed; right: 20px; bottom: 20px; z-index: 2147483000;
  width: 56px; height: 56px; border-radius: 50%; border: none;
  background: var(--ah-accent); color: var(--ah-on-accent);
  box-shadow: 0 6px 20px var(--ah-shadow); display: flex; align-items: center; justify-content: center;
}
.launcher svg { width: 26px; height: 26px; }
.panel {
  position: fixed; right: 20px; bottom: 88px; z-index: 2147483001;
  width: 380px; height: min(600px, calc(100vh - 110px)); max-height: calc(100vh - 110px);
  display: flex; flex-direction: column; overflow: hidden;
  background: var(--ah-bg); border: 1px solid var(--ah-border); border-radius: 14px;
  box-shadow: 0 12px 40px var(--ah-shadow);
}
@media (max-width: 480px) {
  .panel { inset: 0; width: 100%; height: 100%; max-height: none; border-radius: 0; border: none; }
}
.header { display: flex; align-items: center; gap: 6px; padding: 10px 10px 10px 14px;
  background: var(--ah-accent); color: var(--ah-on-accent); }
.header h2 { flex: 1; margin: 0; font-size: 15px; font-weight: 600; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.badge { font-size: 11px; padding: 1px 6px; border-radius: 999px; border: 1px solid currentColor; opacity: 0.85; }
.icon { width: 32px; height: 32px; border: none; border-radius: 8px; background: transparent;
  color: inherit; display: inline-flex; align-items: center; justify-content: center; }
.icon:hover { background: rgba(127, 127, 127, 0.18); }
.icon svg { width: 18px; height: 18px; }
.body { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.msg { max-width: 88%; padding: 8px 12px; border-radius: 12px; word-wrap: break-word; overflow-wrap: anywhere; }
.msg.user { align-self: flex-end; background: var(--ah-accent); color: var(--ah-on-accent); border-bottom-right-radius: 4px; }
.msg.assistant { align-self: flex-start; background: var(--ah-surface); border-bottom-left-radius: 4px; }
.msg p { margin: 0 0 6px; } .msg p:last-child { margin-bottom: 0; }
.msg ul, .msg ol { margin: 4px 0 6px; padding-left: 20px; }
.msg pre { margin: 6px 0; padding: 8px; border-radius: 8px; overflow-x: auto; background: rgba(127, 127, 127, 0.14); }
.msg code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
.msg :not(pre) > code { padding: 0 3px; border-radius: 4px; background: rgba(127, 127, 127, 0.16); }
.msg a { color: inherit; text-decoration: underline; }
.msg .cite { font-size: 11px; opacity: 0.8; }
.msg.user .files, .msg .files { font-size: 12px; opacity: 0.85; margin-top: 4px; }
.msg .note { font-size: 12px; color: var(--ah-muted); margin-top: 4px; font-style: italic; }
.msg .error { color: var(--ah-danger); }
.sources { margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--ah-border); font-size: 12px; }
.sources h3 { margin: 0 0 4px; font-size: 12px; font-weight: 600; color: var(--ah-muted); }
.sources ol { margin: 0; padding-left: 18px; }
.sources li { margin: 2px 0; }
.sources .snippet { display: block; color: var(--ah-muted); }
.divider { align-self: stretch; display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--ah-muted); }
.divider::before, .divider::after { content: ""; flex: 1; border-top: 1px solid var(--ah-border); }
.typing { display: inline-flex; gap: 3px; }
.typing span { width: 6px; height: 6px; border-radius: 50%; background: var(--ah-muted); animation: ah-blink 1.2s infinite; }
.typing span:nth-child(2) { animation-delay: 0.2s; } .typing span:nth-child(3) { animation-delay: 0.4s; }
@keyframes ah-blink { 0%, 80%, 100% { opacity: 0.25; } 40% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .typing span { animation: none; } }
.status { min-height: 18px; padding: 0 14px; font-size: 12px; color: var(--ah-muted); }
.alert { margin: 0 14px 6px; padding: 6px 10px; border-radius: 8px; font-size: 13px;
  color: var(--ah-danger); background: rgba(180, 35, 24, 0.08); }
.composer { border-top: 1px solid var(--ah-border); padding: 8px 10px 10px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.chips:empty { display: none; }
.chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 4px 2px 8px; border-radius: 999px;
  background: var(--ah-surface); border: 1px solid var(--ah-border); font-size: 12px; max-width: 100%; }
.chip span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 180px; }
.chip button { border: none; background: transparent; width: 20px; height: 20px; border-radius: 50%; }
.row { display: flex; align-items: flex-end; gap: 6px; }
textarea { flex: 1; resize: none; min-height: 38px; max-height: 120px; padding: 8px 10px;
  border: 1px solid var(--ah-border); border-radius: 10px; background: var(--ah-bg); }
textarea::placeholder { color: var(--ah-muted); }
.send { height: 38px; min-width: 38px; padding: 0 12px; border: none; border-radius: 10px;
  background: var(--ah-accent); color: var(--ah-on-accent); font-weight: 600; }
.send[disabled] { opacity: 0.5; cursor: default; }
.attach { height: 38px; width: 38px; border: 1px solid var(--ah-border); border-radius: 10px;
  background: var(--ah-bg); display: inline-flex; align-items: center; justify-content: center; }
.attach svg { width: 18px; height: 18px; }
.hidden-input { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.threads { flex: 1; overflow-y: auto; padding: 10px; }
.threads ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.thread { display: flex; align-items: center; gap: 4px; border-radius: 10px; }
.thread.current { background: var(--ah-surface); }
.thread .pick { flex: 1; text-align: left; border: none; background: transparent; padding: 8px 10px; border-radius: 10px; min-width: 0; }
.thread .pick:hover { background: var(--ah-surface); }
.thread .pick strong { display: block; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.thread .pick small { color: var(--ah-muted); font-size: 12px; }
.empty { color: var(--ah-muted); padding: 12px 4px; }
.new-thread { width: 100%; margin-top: 10px; padding: 8px; border-radius: 10px;
  border: 1px dashed var(--ah-border); background: transparent; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); border: 0; }
`;
  }

  // Icons as path data only: drawn with createElementNS, never parsed.
  const ICONS = {
    chat: 'M4 5h16v10H8l-4 4z',
    close: 'M6 6l12 12M18 6L6 18',
    list: 'M4 7h16M4 12h16M4 17h10',
    plus: 'M12 5v14M5 12h14',
    back: 'M15 6l-6 6 6 6',
    clip: 'M16 8l-6.5 6.5a2 2 0 0 0 2.8 2.8L19 10.6a4 4 0 0 0-5.7-5.7L6.6 11.6a6 6 0 0 0 8.5 8.5L20 15.2',
    trash: 'M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12',
    send: 'M5 12h13M13 6l6 6-6 6',
    stop: 'M7 7h10v10H7z',
  };

  function icon(doc, name) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = doc.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    const path = doc.createElementNS(ns, 'path');
    path.setAttribute('d', ICONS[name]);
    svg.appendChild(path);
    return svg;
  }

  // ── storage ─────────────────────────────────────────────────────────────────

  const store = {
    get(key) {
      try { return window.localStorage.getItem(key); } catch { return null; }
    },
    set(key, value) {
      try {
        if (value) window.localStorage.setItem(key, value);
        else window.localStorage.removeItem(key);
      } catch { /* private mode: the visitor starts over next time */ }
    },
  };

  // ── one widget on the page ──────────────────────────────────────────────────

  class ApiError extends Error {
    constructor(status, code, detail, retryAfter) {
      super(detail || code || `HTTP ${status}`);
      this.status = status;
      this.code = code || '';
      this.retryAfter = retryAfter;
    }
  }

  const UNAVAILABLE = new Set(['bad_key', 'origin_not_allowed', 'widget_disabled', 'widget_unavailable']);

  class ChatWidget {
    constructor({ widgetId, publicKey, hub, preview, openAtStart }) {
      this.widgetId = widgetId;
      this.publicKey = publicKey;
      this.hub = hub.replace(/\/+$/, '');
      this.preview = preview || '';
      this.openAtStart = openAtStart;
      this.doc = document;
      this.config = null;
      this.lang = 'en';
      this.token = store.get(this.key('visitor'));
      this.threads = [];
      this.threadId = store.get(this.key('thread'));
      this.files = [];
      this.streaming = null;
      this.loaded = false;
      this.el = {};
    }

    key(name) { return `agents-hub-widget:${this.widgetId}:${name}`; }

    t(key, vars) { return translate(this.lang, key, vars); }

    // ── the API ──

    async request(path, { method = 'GET', body, visitor = true, signal } = {}) {
      const headers = { 'X-Widget-Key': this.publicKey };
      if (this.preview) headers['X-Widget-Preview'] = this.preview;
      if (visitor && this.token) headers['X-Visitor-Token'] = this.token;
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      return fetch(`${this.hub}/api/widgets/public/${encodeURIComponent(this.widgetId)}${path}`, {
        method, headers, signal, credentials: 'omit', mode: 'cors',
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    }

    async errorOf(response) {
      let data = null;
      try { data = await response.json(); } catch { /* not JSON: keep the status */ }
      return new ApiError(response.status, data && data.code, data && data.detail,
        Number(response.headers.get('Retry-After')) || undefined);
    }

    async json(path, options = {}, retried = false) {
      const response = await this.request(path, options);
      if (response.ok) return response.json();
      const error = await this.errorOf(response);
      if (error.code === 'visitor_token_invalid' && !retried) {
        this.token = null;
        await this.ensureVisitor();
        return this.json(path, options, true);
      }
      throw error;
    }

    async ensureVisitor() {
      const response = await this.request('/visitor', { method: 'POST', body: {} });
      if (!response.ok) throw await this.errorOf(response);
      const data = await response.json();
      this.token = data.visitor_token;
      store.set(this.key('visitor'), this.token);
    }

    // ── boot ──

    async boot() {
      let response;
      try {
        response = await this.request('/config', { visitor: false });
      } catch {
        console.warn('[agents-hub-widget] the hub could not be reached at', this.hub);
        return;
      }
      if (!response.ok) {
        const error = await this.errorOf(response);
        // For the site owner: the visitor sees no bubble rather than a broken one.
        console.warn(`[agents-hub-widget] ${this.widgetId} is not available here: ${error.code || error.status}`);
        return;
      }
      this.config = await response.json();
      this.lang = pickLanguage(this.config.language, navigator.languages || [navigator.language]);
      this.render();
      if (this.openAtStart) this.open();
    }

    // ── DOM ──

    make(tag, attrs = {}, children = []) {
      const node = this.doc.createElement(tag);
      for (const [name, value] of Object.entries(attrs)) {
        if (value === undefined || value === null || value === false) continue;
        if (name === 'class') node.className = value;
        else if (name === 'text') node.textContent = value;
        else if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
        else node.setAttribute(name, value === true ? '' : String(value));
      }
      for (const child of [].concat(children)) {
        if (child) node.appendChild(child);
      }
      return node;
    }

    iconButton(name, label, onclick, cls = 'icon') {
      return this.make('button', { type: 'button', class: cls, 'aria-label': label, title: label, onclick },
        [icon(this.doc, name)]);
    }

    render() {
      const host = this.make('div', { 'data-agents-hub-widget': this.widgetId });
      this.doc.body.appendChild(host);
      const shadow = host.attachShadow({ mode: 'closed' });
      this.shadow = shadow;
      const style = this.make('style', { text: styles(this.config.accent) });
      const root = this.make('div', { class: 'root', lang: this.lang });
      shadow.append(style, root);

      const title = this.config.title || this.t('title');
      this.el.launcher = this.iconButton('chat', this.t('open'), () => this.toggle(), 'launcher');
      this.el.launcher.setAttribute('aria-expanded', 'false');
      this.el.launcher.setAttribute('aria-controls', 'ah-panel');

      this.el.threadsButton = this.iconButton('list', this.t('showThreads'), () => this.showThreads(true));
      this.el.backButton = this.iconButton('back', this.t('back'), () => this.showThreads(false));
      this.el.backButton.hidden = true;
      const header = this.make('div', { class: 'header' }, [
        this.el.backButton,
        this.make('h2', { id: 'ah-title', text: title }),
        this.config.preview ? this.make('span', { class: 'badge', text: this.t('preview') }) : null,
        this.el.threadsButton,
        this.iconButton('plus', this.t('newThread'), () => this.newThread()),
        this.iconButton('close', this.t('close'), () => this.close()),
      ]);

      // Conversation view.
      this.el.log = this.make('div', { class: 'log', role: 'log', 'aria-live': 'polite',
        'aria-relevant': 'additions', 'aria-label': this.t('messages'), tabindex: '0' });
      this.el.status = this.make('div', { class: 'status', 'aria-live': 'polite' });
      this.el.alert = this.make('div', { class: 'alert', role: 'alert', hidden: true });
      this.el.chips = this.make('div', { class: 'chips' });
      this.el.input = this.make('textarea', {
        rows: '1', 'aria-label': this.t('message'),
        placeholder: this.config.placeholder || this.t('placeholder'),
        maxlength: String(this.config.limits?.max_message_chars || 8000),
        onkeydown: (event) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            this.send();
          }
        },
        oninput: () => this.autoGrow(),
      });
      this.el.file = this.make('input', {
        type: 'file', multiple: true, class: 'hidden-input', tabindex: '-1', 'aria-hidden': 'true',
        onchange: () => this.addFiles(),
      });
      this.el.attach = this.iconButton('clip', this.t('attach'), () => this.el.file.click(), 'attach');
      this.el.attach.hidden = !(this.config.limits?.max_attachments > 0 && this.config.limits?.attachment_max_bytes > 0);
      this.el.send = this.make('button', { type: 'button', class: 'send', onclick: () => this.sendOrStop() });
      this.setSendButton(false);
      const composer = this.make('div', { class: 'composer' }, [
        this.el.chips,
        this.make('div', { class: 'row' }, [this.el.attach, this.el.input, this.el.send]),
        this.el.file,
      ]);
      this.el.chat = this.make('div', { class: 'body chat' }, [this.el.log, this.el.status, this.el.alert, composer]);

      // Thread list view.
      this.el.threadList = this.make('ul', { 'aria-label': this.t('threads') });
      this.el.threads = this.make('div', { class: 'threads', hidden: true }, [
        this.el.threadList,
        this.make('button', { type: 'button', class: 'new-thread', text: this.t('newThread'),
          onclick: () => this.newThread() }),
      ]);

      this.el.panel = this.make('div', {
        class: 'panel', id: 'ah-panel', role: 'dialog', 'aria-modal': 'true',
        'aria-labelledby': 'ah-title', hidden: true,
        onkeydown: (event) => this.onPanelKey(event),
      }, [header, this.make('div', { class: 'body' }, [this.el.chat, this.el.threads])]);

      root.append(this.el.panel, this.el.launcher);
    }

    autoGrow() {
      const input = this.el.input;
      input.style.height = 'auto';
      input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
    }

    setSendButton(streaming) {
      const button = this.el.send;
      button.textContent = '';
      button.appendChild(icon(this.doc, streaming ? 'stop' : 'send'));
      const label = streaming ? this.t('stop') : this.t('send');
      button.setAttribute('aria-label', label);
      button.title = label;
    }

    // ── open, close, focus ──

    async open() {
      if (!this.el.panel) return;
      this.el.panel.hidden = false;
      this.el.launcher.setAttribute('aria-expanded', 'true');
      this.el.launcher.setAttribute('aria-label', this.t('close'));
      this.el.input.focus();
      if (!this.loaded) {
        this.loaded = true;
        await this.loadThreads();
      }
    }

    close() {
      if (!this.el.panel || this.el.panel.hidden) return;
      this.el.panel.hidden = true;
      this.el.launcher.setAttribute('aria-expanded', 'false');
      this.el.launcher.setAttribute('aria-label', this.t('open'));
      this.el.launcher.focus();
    }

    toggle() {
      if (this.el.panel && this.el.panel.hidden) this.open();
      else this.close();
    }

    focusables() {
      return [...this.el.panel.querySelectorAll('button, textarea, a[href], [tabindex]:not([tabindex="-1"])')]
        .filter((node) => !node.disabled && !node.closest('[hidden]') && node.getClientRects().length > 0);
    }

    onPanelKey(event) {
      if (event.key === 'Escape') {
        event.stopPropagation();
        this.close();
        return;
      }
      if (event.key !== 'Tab') return;
      const nodes = this.focusables();
      if (!nodes.length) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = this.shadow.activeElement;
      if (event.shiftKey && (active === first || !this.el.panel.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    // ── threads ──

    async loadThreads() {
      try {
        // Renews the stored token (same visitor, new expiry), so a visitor
        // who keeps coming back keeps their conversations.
        await this.ensureVisitor();
        const data = await this.json('/threads');
        this.threads = data.threads || [];
      } catch (error) {
        this.showError(error);
        return;
      }
      const known = this.threads.find((th) => th.thread_id === this.threadId);
      if (known) await this.openThread(known.thread_id);
      else this.startEmpty();
      this.renderThreadList();
    }

    startEmpty() {
      this.threadId = null;
      store.set(this.key('thread'), null);
      this.el.log.textContent = '';
      this.greet();
    }

    greet() {
      if (this.config.greeting) this.el.log.appendChild(this.bubble('assistant', this.config.greeting));
    }

    async openThread(threadId) {
      this.threadId = threadId;
      store.set(this.key('thread'), threadId);
      this.el.log.textContent = '';
      try {
        const data = await this.json(`/threads/${encodeURIComponent(threadId)}`);
        const messages = data.messages || [];
        if (!messages.length) this.greet();
        for (const message of messages) this.appendStored(message);
      } catch (error) {
        if (error.code === 'thread_not_found') {
          this.threads = this.threads.filter((th) => th.thread_id !== threadId);
          this.startEmpty();
        } else {
          this.showError(error);
        }
      }
      this.renderThreadList();
      this.scrollDown();
    }

    appendStored(message) {
      const node = this.bubble(message.role, message.text, message.attachments);
      if (message.role === 'assistant') {
        if (message.status === 'stopped') node.appendChild(this.make('div', { class: 'note', text: this.t('stopped') }));
        else if (message.status !== 'ok' && !message.text) {
          node.textContent = '';
          node.appendChild(this.make('div', { class: 'error', text: this.t('errors.failed') }));
        }
        if (message.citations?.length) node.appendChild(this.sources(message.citations));
      }
      this.el.log.appendChild(node);
      if (message.handoff) this.el.log.appendChild(this.divider(message.handoff.to_agent_name));
    }

    renderThreadList() {
      const list = this.el.threadList;
      list.textContent = '';
      if (!this.threads.length) {
        list.appendChild(this.make('li', { class: 'empty', text: this.t('noThreads') }));
        return;
      }
      for (const thread of this.threads) {
        const title = thread.title || this.t('untitled');
        let when = '';
        try {
          when = new Date(thread.updated_at).toLocaleString(this.lang, { dateStyle: 'short', timeStyle: 'short' });
        } catch { /* an unparseable date shows no date */ }
        list.appendChild(this.make('li', { class: `thread${thread.thread_id === this.threadId ? ' current' : ''}` }, [
          this.make('button', {
            type: 'button', class: 'pick', 'aria-current': thread.thread_id === this.threadId ? 'true' : undefined,
            onclick: () => { this.showThreads(false); this.openThread(thread.thread_id); },
          }, [this.make('strong', { text: title }), this.make('small', { text: when })]),
          this.iconButton('trash', this.t('deleteThread', { title }), () => this.deleteThread(thread.thread_id)),
        ]));
      }
    }

    showThreads(show) {
      this.el.threads.hidden = !show;
      this.el.chat.hidden = show;
      this.el.backButton.hidden = !show;
      this.el.threadsButton.hidden = show;
      if (show) {
        this.renderThreadList();
        (this.el.threadList.querySelector('button') || this.el.backButton).focus();
      } else {
        this.el.input.focus();
      }
    }

    newThread() {
      if (this.streaming) return;
      this.showThreads(false);
      this.startEmpty();
      this.clearError();
      this.el.input.focus();
    }

    async deleteThread(threadId) {
      if (!window.confirm(this.t('confirmDelete'))) return;
      try {
        await this.json(`/threads/${encodeURIComponent(threadId)}`, { method: 'DELETE' });
      } catch (error) {
        if (error.code !== 'thread_not_found') {
          this.showError(error);
          return;
        }
      }
      this.threads = this.threads.filter((th) => th.thread_id !== threadId);
      if (threadId === this.threadId) this.startEmpty();
      this.renderThreadList();
      (this.el.threadList.querySelector('button') || this.el.backButton).focus();
    }

    // ── messages ──

    bubble(role, text, files) {
      const node = this.make('div', { class: `msg ${role === 'user' ? 'user' : 'assistant'}` });
      const who = this.make('span', { class: 'sr-only', text: `${role === 'user' ? this.t('you') : this.t('assistant')}: ` });
      node.appendChild(who);
      if (role === 'user') {
        // The visitor's own words are shown as typed: no markup.
        if (text) node.appendChild(this.make('div', { text }));
      } else {
        node.appendChild(renderMarkdown(text, this.doc));
      }
      if (files && files.length) node.appendChild(this.make('div', { class: 'files', text: `📎 ${files.join(', ')}` }));
      return node;
    }

    fill(node, text) {
      node.textContent = '';
      node.appendChild(this.make('span', { class: 'sr-only', text: `${this.t('assistant')}: ` }));
      if (text) node.appendChild(renderMarkdown(text, this.doc));
      else node.appendChild(this.make('span', { class: 'typing', 'aria-label': this.t('working') },
        [this.make('span'), this.make('span'), this.make('span')]));
    }

    divider(name) {
      return this.make('div', { class: 'divider', text: this.t('handoff', { name: name || '' }) });
    }

    sources(citations) {
      const list = this.make('ol');
      for (const source of citations) {
        const item = this.make('li', { value: String(source.n) });
        const label = source.title || `[${source.n}]`;
        item.appendChild(source.url && isSafeUrl(source.url) ? link(this.doc, source.url, label)
          : this.make('span', { text: label }));
        if (source.snippet) item.appendChild(this.make('span', { class: 'snippet', text: source.snippet }));
        list.appendChild(item);
      }
      return this.make('div', { class: 'sources' }, [this.make('h3', { text: this.t('sources') }), list]);
    }

    scrollDown() {
      const log = this.el.log;
      log.scrollTop = log.scrollHeight;
    }

    // ── errors ──

    messageFor(error) {
      if (!(error instanceof ApiError)) return this.t('errors.network');
      if (UNAVAILABLE.has(error.code)) return this.t('errors.unavailable');
      const known = ['rate_limited', 'daily_limit', 'attachments_disabled', 'message_too_long', 'busy',
        'too_many_threads'];
      if (known.includes(error.code)) return this.t(`errors.${error.code}`);
      if (error.code === 'attachment_too_large') return this.t('errors.attachments_too_large');
      if (error.code === 'too_many_attachments') {
        return this.t('errors.too_many_attachments', { count: this.config.limits?.max_attachments || 0 });
      }
      return this.t('errors.generic');
    }

    showError(error) {
      const text = typeof error === 'string' ? error : this.messageFor(error);
      this.el.alert.textContent = text;
      this.el.alert.hidden = false;
    }

    clearError() {
      this.el.alert.textContent = '';
      this.el.alert.hidden = true;
    }

    // ── attachments ──

    addFiles() {
      const limits = this.config.limits || {};
      const picked = [...(this.el.file.files || [])];
      this.el.file.value = '';
      this.clearError();
      for (const file of picked) {
        if (this.files.length >= (limits.max_attachments || 0)) {
          this.showError(this.t('errors.too_many_attachments', { count: limits.max_attachments || 0 }));
          break;
        }
        if (file.size > (limits.attachment_max_bytes || 0)) {
          this.showError(this.t('errors.attachment_too_large',
            { name: file.name, size: formatBytes(limits.attachment_max_bytes || 0) }));
          continue;
        }
        this.files.push(file);
      }
      this.renderChips();
    }

    renderChips() {
      this.el.chips.textContent = '';
      this.files.forEach((file, index) => {
        this.el.chips.appendChild(this.make('span', { class: 'chip' }, [
          this.make('span', { text: file.name, title: file.name }),
          this.make('button', {
            type: 'button', 'aria-label': this.t('removeFile', { name: file.name }), text: '×',
            onclick: () => { this.files.splice(index, 1); this.renderChips(); this.el.input.focus(); },
          }),
        ]));
      });
    }

    readFile(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const result = String(reader.result || '');
          resolve({ name: file.name, mime_type: file.type || null, data_b64: result.slice(result.indexOf(',') + 1) });
        };
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
    }

    // ── a turn ──

    sendOrStop() {
      if (this.streaming) this.streaming.abort();
      else this.send();
    }

    async send() {
      const text = this.el.input.value.trim();
      if ((!text && !this.files.length) || this.streaming) return;
      this.clearError();
      const files = this.files;
      const controller = new AbortController();
      this.streaming = controller;
      this.setSendButton(true);
      let reply = null;
      let replyText = '';
      try {
        if (!this.token) await this.ensureVisitor();
        if (!this.threadId) {
          const thread = await this.json('/threads', { method: 'POST', body: {} });
          this.threadId = thread.thread_id;
          store.set(this.key('thread'), this.threadId);
          this.threads.unshift(thread);
        }
        const attachments = await Promise.all(files.map((file) => this.readFile(file)));
        const mine = this.bubble('user', text, files.map((file) => file.name));
        this.el.log.appendChild(mine);
        this.el.input.value = '';
        this.autoGrow();
        this.files = [];
        this.renderChips();
        reply = this.bubble('assistant', '');
        this.fill(reply, '');
        this.el.log.appendChild(reply);
        this.scrollDown();

        const response = await this.request(`/threads/${encodeURIComponent(this.threadId)}/messages`, {
          method: 'POST', body: { text, attachments }, signal: controller.signal,
        });
        if (!response.ok) {
          // Refused before anything ran: give the visitor their message back.
          const error = await this.errorOf(response);
          reply.remove();
          reply = null;
          mine.remove();
          this.el.input.value = text;
          this.autoGrow();
          this.files = files;
          this.renderChips();
          if (error.code === 'thread_not_found') this.startEmpty();
          this.showError(error);
          return;
        }
        let frame = 0;
        const redraw = () => {
          frame = 0;
          this.fill(reply, replyText);
          this.scrollDown();
        };
        const onEvent = (event) => {
          if (event.type === 'token') {
            replyText += event.token || '';
            if (!frame) frame = window.requestAnimationFrame(redraw);
          } else if (event.type === 'tool_start') {
            this.el.status.textContent = this.t('usingTool', { tool: event.tool || '' });
          } else if (event.type === 'tool_end') {
            this.el.status.textContent = '';
          } else if (event.type === 'handoff') {
            if (frame) { window.cancelAnimationFrame(frame); frame = 0; }
            this.fill(reply, event.from_response || replyText);
            this.el.log.appendChild(this.divider(event.to_agent_name));
            replyText = '';
            reply = this.bubble('assistant', '');
            this.fill(reply, '');
            this.el.log.appendChild(reply);
            this.scrollDown();
          } else if (event.type === 'done') {
            if (frame) { window.cancelAnimationFrame(frame); frame = 0; }
            this.el.status.textContent = '';
            if (event.ok) {
              replyText = event.response || replyText;
              this.fill(reply, replyText);
              if (event.citations?.length) reply.appendChild(this.sources(event.citations));
            } else if (event.error === 'stopped') {
              this.fill(reply, replyText);
              reply.appendChild(this.make('div', { class: 'note', text: this.t('stopped') }));
            } else {
              reply.textContent = '';
              reply.appendChild(this.make('div', { class: 'error', text: this.t('errors.failed') }));
            }
            const current = this.threads.find((th) => th.thread_id === this.threadId);
            if (current && event.title) current.title = event.title;
            reply = null;
            this.scrollDown();
          }
        };
        await readStream(response, onEvent);
        if (reply) {
          // The stream ended without a done: the connection dropped.
          this.fill(reply, replyText);
          this.showError(this.t('errors.network'));
        }
      } catch (error) {
        if (error && error.name === 'AbortError') {
          if (reply) {
            this.fill(reply, replyText);
            reply.appendChild(this.make('div', { class: 'note', text: this.t('stopped') }));
          }
        } else {
          if (reply && !replyText) reply.remove();
          this.showError(error);
        }
      } finally {
        this.streaming = null;
        this.el.status.textContent = '';
        this.setSendButton(false);
        this.renderThreadList();
      }
    }
  }

  /** Read a fetch response's body as SSE, calling onEvent per event. */
  async function readStream(response, onEvent) {
    const parser = createSSEParser(onEvent);
    if (!response.body || !response.body.getReader) {
      parser.push(await response.text());
      parser.end();
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.push(decoder.decode());
    parser.end();
  }

  // ── boot every tag on the page ──────────────────────────────────────────────

  const instances = [];

  function bootScript(script) {
    if (!script || script.dataset.agentsHubBooted) return;
    const widgetId = script.dataset.widget;
    const publicKey = script.dataset.key;
    if (!widgetId || !publicKey) {
      console.warn('[agents-hub-widget] the script tag needs data-widget and data-key');
      return;
    }
    script.dataset.agentsHubBooted = '1';
    let hub = script.dataset.hub;
    if (!hub) {
      try {
        hub = new URL(script.src, window.location.href).origin;
      } catch {
        hub = window.location.origin;
      }
    }
    const widget = new ChatWidget({
      widgetId, publicKey, hub, preview: script.dataset.preview, openAtStart: script.dataset.open === 'true',
    });
    instances.push(widget);
    const start = () => widget.boot();
    if (document.body) start();
    else document.addEventListener('DOMContentLoaded', start, { once: true });
  }

  function forEach(widgetId, fn) {
    instances.filter((w) => !widgetId || w.widgetId === widgetId).forEach(fn);
  }

  const api = window.AgentsHubWidget || {};
  api.open = (widgetId) => forEach(widgetId, (w) => w.open());
  api.close = (widgetId) => forEach(widgetId, (w) => w.close());
  api.toggle = (widgetId) => forEach(widgetId, (w) => w.toggle());
  // The pure parts, for the hub's own tests.
  api.internals = { renderMarkdown, renderInline, createSSEParser, pickLanguage, translate,
    formatBytes, isSafeUrl, STRINGS, ACCENTS };
  window.AgentsHubWidget = api;

  if (typeof document !== 'undefined') {
    if (document.currentScript) bootScript(document.currentScript);
    // A tag manager may insert the tag in a way currentScript does not see:
    // pick up any tag not booted yet.
    document.querySelectorAll('script[data-widget][data-key]').forEach(bootScript);
  }
})();
