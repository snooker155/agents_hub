import { describe, it, expect } from 'vitest';
import { parseSystemPatch } from '../systemPatch';

const MARKER = '<!-- system-patch {"branch": "system/2026-09-24-abc", "repo_dir": "/srv/agents_hub", "commit": "a1b2c3d", "fetch_command": "git fetch origin system/2026-09-24-abc"} -->';

describe('parseSystemPatch', () => {
  it('returns null for an ordinary result with no marker', () => {
    expect(parseSystemPatch('Just a normal result.')).toBeNull();
    expect(parseSystemPatch('')).toBeNull();
    expect(parseSystemPatch(undefined)).toBeNull();
  });

  it('parses the marker into meta and strips it from the body', () => {
    const text = `${MARKER}\n## System patch\n\n\`\`\`diff\n+ line\n\`\`\`\n`;
    const parsed = parseSystemPatch(text);
    expect(parsed).not.toBeNull();
    expect(parsed.meta).toEqual({
      branch: 'system/2026-09-24-abc',
      repo_dir: '/srv/agents_hub',
      commit: 'a1b2c3d',
      fetch_command: 'git fetch origin system/2026-09-24-abc',
    });
    expect(parsed.body).not.toContain('system-patch');
    expect(parsed.body.startsWith('## System patch')).toBe(true);
  });

  it('tolerates leading whitespace before the marker', () => {
    const text = `\n  ${MARKER}\nbody text`;
    const parsed = parseSystemPatch(text);
    expect(parsed).not.toBeNull();
    expect(parsed.body).toBe('body text');
  });

  it('returns null when the marker JSON is malformed', () => {
    expect(parseSystemPatch('<!-- system-patch {not json} -->\nbody')).toBeNull();
  });

  it('returns null when the marker is not the first line', () => {
    const text = `Some text first\n${MARKER}\nbody`;
    expect(parseSystemPatch(text)).toBeNull();
  });
});
