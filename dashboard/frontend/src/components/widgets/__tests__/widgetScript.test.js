// The embeddable widget's pure parts (dashboard/frontend/widget/widget.js):
// the safe renderer, the SSE parser and the language choice. The script is a
// classic IIFE with no exports; importing it runs it, finds no script tag to
// boot, and leaves its internals on window.AgentsHubWidget.
import { describe, it, expect, beforeAll } from 'vitest';
import '../../../../widget/widget.js';

let W;
beforeAll(() => { W = window.AgentsHubWidget.internals; });

const html = (text) => {
  const box = document.createElement('div');
  box.appendChild(W.renderMarkdown(text, document));
  return box;
};

describe('renderMarkdown', () => {
  it('never turns untrusted text into markup', () => {
    const box = html('<img src=x onerror="alert(1)"> **<b>bold</b>** `<script>`');
    expect(box.querySelector('img')).toBeNull();
    expect(box.querySelector('b')).toBeNull();
    expect(box.querySelector('script')).toBeNull();
    expect(box.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(box.querySelector('strong').textContent).toBe('<b>bold</b>');
    expect(box.querySelector('code').textContent).toBe('<script>');
  });

  it('links only http and https, in a new tab without an opener', () => {
    const box = html('See [docs](https://example.com/a?b=1) and https://x.example/path. Not [this](javascript:alert(1)).');
    const links = [...box.querySelectorAll('a')];
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://example.com/a?b=1', 'https://x.example/path']);
    links.forEach((a) => {
      expect(a.target).toBe('_blank');
      expect(a.rel).toBe('noopener noreferrer');
    });
    expect(links[0].textContent).toBe('docs');
    expect(box.textContent).toContain('[this](javascript:alert(1))');
    // The full stop after a bare URL is prose, not part of the link.
    expect(links[1].textContent).toBe('https://x.example/path');
  });

  it('draws paragraphs, line breaks, lists, code blocks and source markers', () => {
    const box = html('First line\nsecond line\n\n- one\n- two\n\n1. a\n2. b\n\n```js\nconst x = "<y>";\n```\nAs said [2].');
    const paragraphs = box.querySelectorAll('p');
    expect(paragraphs[0].querySelector('br')).not.toBeNull();
    expect([...box.querySelectorAll('ul li')].map((li) => li.textContent)).toEqual(['one', 'two']);
    expect([...box.querySelectorAll('ol li')].map((li) => li.textContent)).toEqual(['a', 'b']);
    expect(box.querySelector('pre code').textContent).toBe('const x = "<y>";');
    expect(box.querySelector('sup.cite').textContent).toBe('[2]');
  });

  it('keeps an unterminated code block as code', () => {
    const box = html('```\nstill streaming');
    expect(box.querySelector('pre code').textContent).toBe('still streaming');
  });
});

describe('isSafeUrl', () => {
  it('accepts http(s) only', () => {
    expect(W.isSafeUrl('https://a.example')).toBe(true);
    expect(W.isSafeUrl('http://localhost:3000/x')).toBe(true);
    expect(W.isSafeUrl('javascript:alert(1)')).toBe(false);
    expect(W.isSafeUrl('data:text/html,hi')).toBe(false);
    expect(W.isSafeUrl('/relative')).toBe(false);
  });
});

describe('createSSEParser', () => {
  const collect = (chunks) => {
    const events = [];
    const parser = W.createSSEParser((e) => events.push(e));
    chunks.forEach((c) => parser.push(c));
    parser.end();
    return events;
  };

  it('parses events cut anywhere across chunks', () => {
    const stream = 'data: {"type":"token","token":"Hel"}\n\ndata: {"type":"token","token":"lo"}\n\ndata: {"type":"done","ok":true}\n\n';
    for (const size of [1, 3, 7, 50]) {
      const chunks = [];
      for (let i = 0; i < stream.length; i += size) chunks.push(stream.slice(i, i + size));
      expect(collect(chunks).map((e) => e.type)).toEqual(['token', 'token', 'done']);
    }
  });

  it('handles CRLF, a CR split from its LF, comments and multi-line data', () => {
    const events = collect([
      ': ping\r\n\r\n',
      'event: message\r\ndata: {"type":"meta",\r',
      '\ndata: "run_id":"r1"}\r\n\r\n',
      'data: not json\n\n',
      'data: {"type":"done"}',
    ]);
    expect(events).toEqual([{ type: 'meta', run_id: 'r1' }, { type: 'done' }]);
  });
});

describe('language and strings', () => {
  it('picks the configured language, else the browser, else English', () => {
    expect(W.pickLanguage('de', ['ru-RU'])).toBe('de');
    expect(W.pickLanguage('auto', ['fr-FR', 'ru-RU'])).toBe('ru');
    expect(W.pickLanguage('auto', ['fr-FR'])).toBe('en');
    expect(W.pickLanguage(undefined, [])).toBe('en');
  });

  it('carries the same keys in every language', () => {
    const keys = (obj, prefix = '') => Object.entries(obj).flatMap(([k, v]) => (
      typeof v === 'object' ? keys(v, `${prefix}${k}.`) : [`${prefix}${k}`])).sort();
    const en = keys(W.STRINGS.en);
    expect(keys(W.STRINGS.ru)).toEqual(en);
    expect(keys(W.STRINGS.de)).toEqual(en);
  });

  it('interpolates and falls back to English', () => {
    expect(W.translate('ru', 'handoff', { name: 'Billing' })).toBe('Разговор передан: Billing');
    expect(W.translate('xx', 'send')).toBe('Send');
    expect(W.formatBytes(2 * 1024 * 1024)).toBe('2 MB');
    expect(W.formatBytes(1536)).toBe('2 KB');
  });
});
