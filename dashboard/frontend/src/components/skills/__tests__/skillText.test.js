import { describe, expect, it } from 'vitest';
import { snapshotText } from '../skillText';

const t = (key) => key.split('.').pop();

describe('snapshotText', () => {
  it('lists the files of a repository skill with their hashes, so a changed script is a changed line', () => {
    const before = snapshotText({ name: 'milk-check', resources: ['scripts/check.sh'], files: { 'scripts/check.sh': 'aaaaaaaaaaaa1111' } }, t);
    const after = snapshotText({ name: 'milk-check', resources: ['scripts/check.sh'], files: { 'scripts/check.sh': 'bbbbbbbbbbbb2222' } }, t);
    expect(before).toContain('scripts/check.sh aaaaaaaaaaaa');
    expect(before).not.toBe(after);
  });

  it('keeps the one-line file list for a skill without hashes', () => {
    expect(snapshotText({ name: 'n', resources: ['a.md', 'b.md'] }, t)).toContain('fieldFiles: a.md, b.md');
  });
});
