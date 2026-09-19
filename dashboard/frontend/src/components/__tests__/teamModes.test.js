import { describe, it, expect } from 'vitest';
import {
  MODES,
  MODE_BADGE,
  STATUS_STYLES,
  isLive,
  modeHelp,
  modeLabel,
  modeOptions,
  modeSummary,
} from '../teamModes';

// The helpers only resolve keys — the wording lives in the locale files.
const t = (key) => `t(${key})`;

describe('mode wording', () => {
  it('resolves each mode through the teams.modes namespace', () => {
    expect(modeLabel('centralized', t)).toBe('t(teams.modes.centralized.label)');
    expect(modeSummary('autonomous', t)).toBe('t(teams.modes.autonomous.summary)');
    expect(modeHelp('parallel', t)).toBe('t(teams.modes.parallel.help)');
  });

  it('shows an unknown mode verbatim rather than a missing-key string', () => {
    expect(modeLabel('experimental', t)).toBe('experimental');
  });

  it('has no explanation for an unknown mode', () => {
    expect(modeSummary('experimental', t)).toBe('');
    expect(modeHelp('experimental', t)).toBe('');
  });

  it('offers exactly the known modes, in order', () => {
    expect(modeOptions(t)).toEqual(MODES.map((value) => ({
      value,
      label: `t(teams.modes.${value}.option)`,
    })));
  });
});

describe('badges', () => {
  it('styles every mode', () => {
    MODES.forEach((mode) => expect(MODE_BADGE[mode]).toEqual(expect.any(String)));
  });

  it('styles every run status', () => {
    ['running', 'stopping', 'completed', 'stopped', 'failed'].forEach((s) => {
      expect(STATUS_STYLES[s]).toEqual(expect.any(String));
    });
  });
});

describe('isLive', () => {
  it('counts a stopping run as still live — it has not finished yet', () => {
    expect(isLive('running')).toBe(true);
    expect(isLive('stopping')).toBe(true);
  });

  it('counts every terminal status as not live', () => {
    ['completed', 'stopped', 'failed', undefined, null, ''].forEach((s) => {
      expect(isLive(s)).toBe(false);
    });
  });
});
