import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// A take of nothing but silence never reaches the transcription model: given
// silence, Whisper writes subtitle credits, which would be sent as the message.

const recorder = {
  supported: true, level: 0, error: '',
  start: vi.fn(() => Promise.resolve()),
  stop: vi.fn(),
  cancel: vi.fn(),
  wasSilent: vi.fn(),
};
vi.mock('../../assistant/useRecorder', () => ({ default: () => recorder }));
vi.mock('../../assistant/useBrowserRecognition', () => ({ default: () => ({ available: false }) }));

const transcribeRecording = vi.fn();
vi.mock('../../../api/assistant', () => ({
  AssistantError: class extends Error {},
  getAssistantVoice: () => Promise.resolve({ data: { voice: { transcription: { model: 'whisper' } } } }),
  transcribeRecording: (...args) => transcribeRecording(...args),
}));

import useChatVoice from '../useChatVoice';

async function speak({ take, silent }) {
  recorder.stop.mockResolvedValue(take);
  recorder.wasSilent.mockReturnValue(silent);
  const onText = vi.fn();
  const onError = vi.fn();
  const { result } = renderHook(() => useChatVoice({ workspace: 'ws', language: 'ru', onText, onError }));
  await waitFor(() => expect(result.current.available).toBe(true));
  await act(async () => { await result.current.start(); });
  await act(async () => { await result.current.end(); });
  return { onText, onError };
}

describe('useChatVoice', () => {
  beforeEach(() => { transcribeRecording.mockReset(); });

  it('says nothing was heard for a silent take and does not transcribe it', async () => {
    const { onText, onError } = await speak({ take: null, silent: true });
    expect(transcribeRecording).not.toHaveBeenCalled();
    expect(onText).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledWith('nothing');
  });

  it('stays quiet for a click', async () => {
    const { onError } = await speak({ take: null, silent: false });
    expect(transcribeRecording).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it('sends what was said', async () => {
    transcribeRecording.mockResolvedValue({ text: 'покажи запуски' });
    const { onText } = await speak({ take: { blob: new Blob(['x']), type: 'audio/webm', seconds: 2 }, silent: false });
    expect(onText).toHaveBeenCalledWith('покажи запуски');
  });
});
