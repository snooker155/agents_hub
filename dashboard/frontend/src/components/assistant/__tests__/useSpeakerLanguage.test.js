import { renderHook, act, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import useSpeaker from '../useSpeaker';

// The browser's own voice reads one answer in one language: the first
// sentence decides it, a sentence in another language inside the same turn
// keeps it, and the next turn decides afresh.
describe('useSpeaker with the browser voice', () => {
  const spoken = [];
  beforeEach(() => {
    spoken.length = 0;
    window.SpeechSynthesisUtterance = function Utterance(text) { this.text = text; };
    window.speechSynthesis = {
      speak: (u) => { spoken.push({ text: u.text, lang: u.lang }); setTimeout(() => u.onend?.(), 0); },
      cancel: vi.fn(),
    };
  });
  afterEach(() => {
    delete window.SpeechSynthesisUtterance;
    delete window.speechSynthesis;
  });

  it('keeps one locale for every sentence of a turn', async () => {
    const { result } = renderHook(() => useSpeaker({ serverSpeech: false, language: 'en' }));
    act(() => {
      result.current.say({ run_id: 'r1', text: 'Привет, я ваш ассистент.' });
      result.current.say({ run_id: 'r1', text: 'I can speak English too.' });
      result.current.say({ run_id: 'r2', text: 'Hello, how are you today?' });
    });
    await waitFor(() => expect(spoken).toHaveLength(3));
    expect(spoken.map((s) => s.lang)).toEqual(['ru-RU', 'ru-RU', 'en-US']);
  });
});
