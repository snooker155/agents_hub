import { describe, it, expect } from 'vitest';
import { clampEffort } from '../effort';

// The client's clamp mirrors providers/reasoning_profile.clamp_effort, so a
// picker shows what the server will actually send.
const gpt5 = { off: 'minimal', efforts: ['minimal', 'low', 'medium', 'high'] };
const opus48 = { off: null, efforts: ['off', 'low', 'medium', 'high', 'xhigh', 'max'] };

describe('clampEffort', () => {
  it('keeps an effort the model accepts', () => {
    expect(clampEffort(gpt5, 'medium')).toBe('medium');
  });
  it('takes the nearest accepted below, else the lowest', () => {
    expect(clampEffort(gpt5, 'xhigh')).toBe('high');
    expect(clampEffort(gpt5, 'none')).toBe('minimal');
  });
  it('sends what the profile sends for off', () => {
    expect(clampEffort(gpt5, 'off')).toBe('minimal');
    expect(clampEffort(opus48, 'off')).toBe('off');
  });
  it('has nothing for no profile or an unknown word', () => {
    expect(clampEffort(null, 'high')).toBeNull();
    expect(clampEffort(gpt5, 'loud')).toBeNull();
  });
});
