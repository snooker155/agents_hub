import { describe, it, expect } from 'vitest';
import {
  BLOCKED_COMBINATIONS,
  CAPABILITY_ORDER,
  capabilitySources,
  checkCombination,
} from '../capabilities';

// The tool metadata the editor gets from GET /api/tools, trimmed to what the
// guard reads.
const TOOLS = {
  web_fetch: { capabilities: ['ingests_untrusted'] },
  read_email: { capabilities: ['ingests_untrusted'] },
  read_memory: { capabilities: ['reads_private'] },
  read_file: { capabilities: ['reads_private'] },
  send_email: { capabilities: ['can_exfiltrate'] },
  calculator: { capabilities: [] },
  no_meta: {},
};

describe('capabilitySources', () => {
  it('maps each capability to the tools that granted it', () => {
    expect(capabilitySources(['web_fetch', 'read_memory'], TOOLS)).toEqual({
      ingests_untrusted: ['web_fetch'],
      reads_private: ['read_memory'],
    });
  });

  it('collects every granting tool under the same capability', () => {
    expect(capabilitySources(['web_fetch', 'read_email'], TOOLS).ingests_untrusted)
      .toEqual(['web_fetch', 'read_email']);
  });

  it('never lists the same tool twice', () => {
    expect(capabilitySources(['web_fetch', 'web_fetch'], TOOLS).ingests_untrusted)
      .toEqual(['web_fetch']);
  });

  it('ignores tools with no capabilities and survives missing metadata', () => {
    expect(capabilitySources(['calculator', 'no_meta', 'unknown_tool'], TOOLS)).toEqual({});
    expect(capabilitySources(null, null)).toEqual({});
  });
});

describe('checkCombination', () => {
  it('passes an empty selection', () => {
    expect(checkCombination([], TOOLS)).toBeNull();
  });

  // These two pairs stay legal on purpose — neither closes the loop.
  it('allows reads_private + can_exfiltrate', () => {
    expect(checkCombination(['read_file', 'send_email'], TOOLS)).toBeNull();
  });

  it('allows reads_private + ingests_untrusted', () => {
    expect(checkCombination(['read_file', 'web_fetch'], TOOLS)).toBeNull();
  });

  it('warns on an exfiltration path (untrusted in, data out)', () => {
    const hit = checkCombination(['web_fetch', 'send_email'], TOOLS);
    expect(hit.id).toBe('exfiltration_path');
    expect(hit.severity).toBe('warn');
    expect(hit.blocking).toBe(false);
  });

  it('blocks the lethal trifecta', () => {
    const hit = checkCombination(['web_fetch', 'read_memory', 'send_email'], TOOLS);
    expect(hit.id).toBe('lethal_trifecta');
    expect(hit.severity).toBe('block');
    expect(hit.blocking).toBe(true);
  });

  it('reports the blocking rule when a selection matches two rules at once', () => {
    // web_fetch + send_email alone would match exfiltration_path; adding
    // read_memory means both rules match and the blocking one must win.
    const selection = ['web_fetch', 'read_memory', 'send_email'];
    const held = Object.keys(capabilitySources(selection, TOOLS));
    const matched = BLOCKED_COMBINATIONS.filter(
      (r) => r.capabilities.every((c) => held.includes(c)),
    );
    expect(matched).toHaveLength(2);
    expect(checkCombination(selection, TOOLS).id)
      .toBe(matched.find((r) => r.severity === 'block').id);
  });

  it('lists blocking rules ahead of warnings', () => {
    // checkCombination falls back to the first match, so a warning rule placed
    // ahead of a blocking one would downgrade an overlapping selection without
    // any other test noticing.
    const severities = BLOCKED_COMBINATIONS.map((r) => r.severity);
    expect(severities.indexOf('warn') === -1 || severities.lastIndexOf('block') === -1
      || severities.lastIndexOf('block') < severities.indexOf('warn')).toBe(true);
  });

  it('names the offending tools in capability order', () => {
    const hit = checkCombination(['send_email', 'read_memory', 'web_fetch'], TOOLS);
    expect(hit.message).toBe(
      'ingests untrusted content (web_fetch) + reads private data (read_memory)'
      + ' + can send data outside (send_email)',
    );
    expect(hit.sources).toEqual({
      ingests_untrusted: ['web_fetch'],
      reads_private: ['read_memory'],
      can_exfiltrate: ['send_email'],
    });
  });

  it('only reports the capabilities its own rule names', () => {
    const hit = checkCombination(['web_fetch', 'send_email'], TOOLS);
    expect(Object.keys(hit.sources).sort()).toEqual(['can_exfiltrate', 'ingests_untrusted']);
    expect(hit.message).not.toContain('reads private data');
  });

  it('keeps every rule expressible in the declared capability order', () => {
    BLOCKED_COMBINATIONS.forEach((rule) => {
      rule.capabilities.forEach((c) => expect(CAPABILITY_ORDER).toContain(c));
    });
  });
});
