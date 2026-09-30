import { describe, expect, it } from 'vitest';
import { parseToolOutput, repairTruncatedJson } from '../toolFormatters';

describe('repairTruncatedJson', () => {
  it.each([
    ['{"ok": true, "agents": [{"id": "a", "desc": "Manages the ta', '{"ok": true, "agents": [{"id": "a", "desc": "Manages the ta"}]}'],
    ['{"a": 1, "b', '{"a": 1}'],
    ['{"a": 1, "b":', '{"a": 1}'],
    ['{"a": [1, 2, tr', '{"a": [1, 2]}'],
    ['{"a": 12.', '{"a": 12}'],
    ['{"a": "x\\', '{"a": "x"}'],
    ['[{"k": {"n": [1,', '[{"k": {"n": [1]}}]'],
    ['{"ok": true}', '{"ok": true}'],
  ])('%s', (cut, whole) => {
    expect(repairTruncatedJson(cut)).toBe(whole);
    expect(() => JSON.parse(repairTruncatedJson(cut))).not.toThrow();
  });

  it('leaves text that is not JSON alone', () => {
    expect(repairTruncatedJson('plain words')).toBeNull();
  });
});

describe('parseToolOutput', () => {
  it('parses a whole result', () => {
    expect(parseToolOutput('{"ok": true}')).toEqual({ value: { ok: true }, truncated: false });
  });

  it('parses the head of a result the run log cut short, and says so', () => {
    expect(parseToolOutput('{"ok": true, "items": [{"a": 1}, {"a": "long te... (truncated)')).toEqual({
      value: { ok: true, items: [{ a: 1 }, { a: 'long te' }] }, truncated: true,
    });
  });

  it('reads a cut Python literal too', () => {
    expect(parseToolOutput("{'ok': True, 'x': None, 'name': 'abc... (truncated)")).toEqual({
      value: { ok: true, x: null, name: 'abc' }, truncated: true,
    });
  });

  it('keeps plain text as text', () => {
    expect(parseToolOutput('done... (truncated)')).toEqual({ value: 'done... (truncated)', truncated: false });
  });
});
