import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import WakeListener from '../WakeListener';
import { PREFS_KEY } from '../assistantState';

const api = vi.hoisted(() => {
  class AssistantError extends Error {
    constructor(status, detail) {
      super(detail?.message || String(status));
      this.code = detail?.code || '';
    }
  }
  return { AssistantError, getAssistant: vi.fn(), transcribeRecording: vi.fn() };
});
vi.mock('../../../api/assistant', () => api);

const ear = vi.hoisted(() => ({
  opts: null, start: vi.fn(() => Promise.resolve(true)), stop: vi.fn(), tune: vi.fn(), reset: vi.fn(),
  listening: true, level: 0, speech: false, error: '', engine: 'server',
}));
vi.mock('../useHandsFree', () => ({
  default: (opts) => { ear.opts = opts; return ear; },
  handsFreeSupported: () => true,
}));
vi.mock('../chime', () => ({ chime: vi.fn() }));
vi.mock('../../workspace', () => ({ useWorkspace: () => ({ selectedWorkspace: 'team' }) }));

function Where() {
  const location = useLocation();
  return <div data-testid="where">{location.pathname}|{location.state?.wake?.command ?? ''}</div>;
}

const show = (path = '/tasks') => render(
  <MemoryRouter initialEntries={[path]}>
    <I18nProvider>
      <Routes><Route path="*" element={<><Where /><WakeListener /></>} /></Routes>
    </I18nProvider>
  </MemoryRouter>,
);

describe('the wake phrase on other pages', () => {
  beforeEach(() => {
    localStorage.removeItem(PREFS_KEY);
    ear.start.mockClear();
    api.getAssistant.mockReset().mockResolvedValue({
      data: { home: 'default', workspaces: ['default', 'team'], voice: { transcription: { model: 'stt' } } },
    });
    api.transcribeRecording.mockReset();
  });

  it('is off unless wake mode was chosen on the Assistant page', async () => {
    show();
    expect(screen.queryByTestId('wake-listener')).toBeNull();
    expect(api.getAssistant).not.toHaveBeenCalled();
  });

  it('opens the assistant with what followed the name', async () => {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ listen: 'wake' }));
    show();
    expect(await screen.findByTestId('wake-listener')).toHaveTextContent('Listening for "assistant"');
    await waitFor(() => expect(ear.start).toHaveBeenCalled());
    api.transcribeRecording.mockResolvedValueOnce({ text: 'just talking about the assistant' });
    await act(async () => { ear.opts.onSegment({ blob: new Blob(['x']) }); });
    expect(screen.getByTestId('where')).toHaveTextContent('/tasks|');
    api.transcribeRecording.mockResolvedValueOnce({ text: 'Assistant, open the costs' });
    await act(async () => { ear.opts.onSegment({ blob: new Blob(['x']) }); });
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/assistant|open the costs'));
    expect(api.transcribeRecording).toHaveBeenCalledWith(expect.any(Blob), expect.objectContaining({ workspace: 'team', purpose: 'wake' }));
    // The Assistant page listens for itself.
    expect(screen.queryByTestId('wake-listener')).toBeNull();
  });

  it('can be turned off from the pill', async () => {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ listen: 'wake' }));
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Stop listening for the wake phrase' }));
    expect(screen.queryByTestId('wake-listener')).toBeNull();
    expect(JSON.parse(localStorage.getItem(PREFS_KEY)).listen).toBe('hold');
  });
});
