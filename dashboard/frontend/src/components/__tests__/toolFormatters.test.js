import { describe, it, expect } from 'vitest';
import { parseToolInput, toolInline } from '../toolFormatters';

describe('parseToolInput', () => {
  it('passes an object through untouched', () => {
    const args = { path: 'a.py' };
    expect(parseToolInput(args)).toBe(args);
  });

  it('parses compact JSON, which is what the backend normally sends', () => {
    expect(parseToolInput('{"path":"a.py","lines":10}')).toEqual({ path: 'a.py', lines: 10 });
  });

  it('parses a JSON array', () => {
    expect(parseToolInput('[1,2,3]')).toEqual([1, 2, 3]);
  });

  it('tolerates a Python-literal repr', () => {
    expect(parseToolInput("{'path': 'a.py', 'ok': True, 'gone': False, 'x': None}"))
      .toEqual({ path: 'a.py', ok: true, gone: false, x: null });
  });

  it('keeps double-quoted strings intact while fixing Python keywords', () => {
    // The quote swap is skipped here so "it's fine" is not clobbered.
    expect(parseToolInput('{"note": "it\'s fine", "ok": True}'))
      .toEqual({ note: "it's fine", ok: true });
  });

  it('returns a plain scalar string as-is', () => {
    expect(parseToolInput('just some text')).toBe('just some text');
  });

  it('falls back to the raw string when nothing parses', () => {
    expect(parseToolInput('{broken: ,}')).toBe('{broken: ,}');
  });

  it('returns null for empty input', () => {
    expect(parseToolInput(null)).toBeNull();
    expect(parseToolInput(undefined)).toBeNull();
    expect(parseToolInput('   ')).toBeNull();
  });
});

describe('toolInline', () => {
  it('shows a file tool as the file name only', () => {
    expect(toolInline('read_file', '{"path":"/home/a/b/read_me.py"}')).toBe('read_me.py');
    expect(toolInline('write_file', { file_path: 'src/deep/mod.js' })).toBe('mod.js');
  });

  it('handles Windows-style separators', () => {
    expect(toolInline('read_file', { path: 'C:\\work\\notes.md' })).toBe('notes.md');
  });

  it('shows a shell call as its command', () => {
    expect(toolInline('run_shell', { command: 'ls -la /tmp' })).toBe('ls -la /tmp');
  });

  it('formats a search as a regex literal, with the glob when present', () => {
    expect(toolInline('search_text', { pattern: 'TODO' })).toBe('/TODO/');
    expect(toolInline('search_text', { pattern: 'TODO', file_glob: '*.py' })).toBe('/TODO/ in *.py');
  });

  it('counts the files a diff touches', () => {
    const diff = '--- a/x\n+++ b/x\n@@\n-1\n+2\n--- a/y\n+++ b/y\n@@\n-3\n+4\n';
    expect(toolInline('apply_unified_diff', { diff_text: diff })).toBe('2 files');
    expect(toolInline('apply_unified_diff', { diff_text: '--- a/x\n+++ b/x\n' })).toBe('1 file');
    expect(toolInline('apply_unified_diff', {})).toBe('diff');
  });

  it('renders a relation as an arrow', () => {
    expect(toolInline('graph_add_edge', { source: 'a', target: 'b' })).toBe('a → b');
    expect(toolInline('assign_agent_tool', { agent_id: 'swe', task_id: 't1' })).toBe('swe → t1');
  });

  it('falls back to one side when the other is missing', () => {
    expect(toolInline('graph_add_edge', { source: 'a' })).toBe('a');
    expect(toolInline('graph_add_edge', {})).toBe('');
  });

  it('joins the ids of a sequence', () => {
    expect(toolInline('create_sequence', { task_ids: ['t1', 't2'] })).toBe('t1, t2');
  });

  it('annotates a blocked task with its reason', () => {
    expect(toolInline('block_task', { id: 't1', reason: 'needs key' })).toBe('t1 (needs key)');
    expect(toolInline('block_task', { id: 't1' })).toBe('t1');
  });

  it('falls back to the first argument for an unknown tool', () => {
    expect(toolInline('some_new_tool', { query: 'weather', limit: 5 })).toBe('weather');
    expect(toolInline('some_new_tool', { path: '/a/b/c.txt' })).toBe('c.txt');
  });

  it('falls back to the raw value when a known tool gets an unparsed string', () => {
    expect(toolInline('read_file', 'plain string')).toBe('plain string');
  });

  it('collapses whitespace onto one line', () => {
    expect(toolInline('run_shell', { command: 'echo a\n  echo b' })).toBe('echo a echo b');
  });

  it('truncates past the max with an ellipsis', () => {
    const out = toolInline('run_shell', { command: 'x'.repeat(300) });
    expect(out).toHaveLength(121);
    expect(out.endsWith('…')).toBe(true);
    expect(toolInline('run_shell', { command: 'x'.repeat(300) }, 10)).toBe(`${'x'.repeat(10)}…`);
  });

  it('returns an empty string when there is nothing to show', () => {
    expect(toolInline('read_file', null)).toBe('');
    expect(toolInline('unknown', {})).toBe('');
  });
});
