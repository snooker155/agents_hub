import { describe, expect, it } from 'vitest';
import { replyBlockLabel, replyCodeBlocks, suggestBlockName } from '../replyCode';

describe('replyCodeBlocks', () => {
  it('lists the fenced blocks of agent replies with language, filename and run', () => {
    const messages = [
      { id: 'u1', role: 'user', content: '```js\nnot mine\n```' },
      { id: 'a1', role: 'agent', run_id: 'r1', content: 'Here:\n```python main.py\nprint(1)\n```\nand\n```\nplain\n```' },
      { id: 'a2', role: 'agent', run_id: 'r2', content: 'no code' },
    ];
    const blocks = replyCodeBlocks(messages);
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toMatchObject({ id: 'reply:a1:0', run_id: 'r1', language: 'python', filename: 'main.py', name: 'main.py', body: 'print(1)' });
    expect(blocks[1]).toMatchObject({ id: 'reply:a1:1', language: '', filename: '', name: 'snippet.txt', body: 'plain' });
    expect(replyBlockLabel(blocks[0])).toBe('main.py');
    expect(replyBlockLabel({ language: '', filename: '', body: 'plain' })).toBe('text · plain');
  });

  it('skips empty fences', () => {
    expect(replyCodeBlocks([{ role: 'agent', content: '```\n\n```' }])).toEqual([]);
  });

  it('names a block after the prompt it answers, and numbers a repeat', () => {
    const messages = [
      { id: 'u1', role: 'user', content: 'Напиши, пожалуйста, скрипт: парсер логов nginx' },
      { id: 'a1', role: 'agent', run_id: 'r1', content: '```python\nimport re\nprint(re.compile("x"))\n```\n```python\nimport sys\n```' },
      { id: 'u2', role: 'user', content: 'now the same in JS please' },
      { id: 'u2s', role: 'user', steer: { status: 'queued' }, content: 'and shorter' },
      { id: 'a2', role: 'agent', run_id: 'r2', content: '```js\nconsole.log(1)\n```' },
    ];
    const names = replyCodeBlocks(messages).map((b) => b.name);
    expect(names).toEqual(['парсер_логов_nginx.py', 'парсер_логов_nginx-2.py', 'same-js.js']);
  });
});

describe('suggestBlockName', () => {
  const block = (language, body, filename = '') => ({ language, body, filename });

  it('keeps the filename the fence gave', () => {
    expect(suggestBlockName(block('python', 'x = 1', 'tool.py'), 'write a tool')).toBe('tool.py');
  });

  it('takes a file named in a leading comment', () => {
    expect(suggestBlockName(block('python', '# file: scripts/backup.py\nimport os'), '')).toBe('backup.py');
    expect(suggestBlockName(block('javascript', '// app.js\nconst a = 1'), '')).toBe('app.js');
    expect(suggestBlockName(block('html', '<!-- index.html -->\n<div/>'), '')).toBe('index.html');
  });

  it('takes the first class or function, in the language\'s case', () => {
    expect(suggestBlockName(block('python', 'import os\n\ndef bubble_sort(xs):\n    return xs\n\ndef main():\n    pass'), 'sort it')).toBe('bubble_sort.py');
    expect(suggestBlockName(block('python', 'def helper():\n    pass\n\nclass LogParser:\n    pass'), '')).toBe('log_parser.py');
    expect(suggestBlockName(block('typescript', 'export default function useThing() {}'), '')).toBe('use-thing.ts');
    expect(suggestBlockName(block('javascript', 'const fetchUsers = async () => {}'), '')).toBe('fetch-users.js');
    expect(suggestBlockName(block('go', 'package main\n\nfunc main() {}'), '')).toBe('main.go');
    expect(suggestBlockName(block('rust', 'pub struct Config {}\nfn main() {}'), '')).toBe('config.rs');
    expect(suggestBlockName(block('sql', 'CREATE TABLE IF NOT EXISTS orders (id int);'), '')).toBe('orders.sql');
  });

  it('falls back to the prompt, then to "snippet"', () => {
    expect(suggestBlockName(block('bash', 'ls -la'), 'Give me a script to list files in the folder')).toBe('list-files-folder.sh');
    expect(suggestBlockName(block('bash', 'ls -la'), 'Please, make it')).toBe('snippet.sh');
    expect(suggestBlockName(block('', 'plain'), '')).toBe('snippet.txt');
    expect(suggestBlockName(block('dockerfile', 'FROM python'), 'container for the api')).toBe('container-api.dockerfile');
  });
});
