import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../../i18n';
import RuntimeSection from '../RuntimeSection';

// Several models loading at once: each row keeps its spinner until its own
// call returns, not only the row pressed last.

const getRuntimeStatus = vi.fn();
const loadRuntimeModel = vi.fn();

vi.mock('../../../api/localModels', async (importOriginal) => ({
  ...(await importOriginal()),
  getRuntimeStatus: (...a) => getRuntimeStatus(...a),
  loadRuntimeModel: (...a) => loadRuntimeModel(...a),
}));
vi.mock('../SpeechEngines', () => ({ default: () => null }));
vi.mock('../SpeechPackages', () => ({ default: () => null }));
vi.mock('../ModelSearch', () => ({ default: () => null }));

const speech = (file, engine) => ({ file, name: file, kind: 'speech', engine, loaded: false, loadable: true });

describe('RuntimeSection', () => {
  beforeEach(() => {
    getRuntimeStatus.mockReset();
    loadRuntimeModel.mockReset();
  });

  it('keeps a spinner on every model still loading', async () => {
    getRuntimeStatus.mockResolvedValue({ data: {
      configured: true, ok: true, url: 'http://runtime', engines: {},
      models: [speech('kokoro-v1.0', 'kokoro'), speech('faster-whisper-small', 'whisper')],
    } });
    const pending = {};
    loadRuntimeModel.mockImplementation((file) => new Promise((resolve) => { pending[file] = resolve; }));
    const { container } = render(
      <MemoryRouter><I18nProvider><RuntimeSection jobs={[]} /></I18nProvider></MemoryRouter>);
    const kokoro = await screen.findByTestId('runtime-load-kokoro-v1.0');
    const whisper = screen.getByTestId('runtime-load-faster-whisper-small');
    fireEvent.click(kokoro);
    fireEvent.click(whisper);
    const spinning = (el) => !!el.querySelector('.animate-spin');
    await waitFor(() => expect(spinning(kokoro) && spinning(whisper)).toBe(true));
    pending['faster-whisper-small']({ data: {} });
    await waitFor(() => expect(spinning(whisper)).toBe(false));
    expect(spinning(kokoro)).toBe(true);
    expect(container).toBeTruthy();
  });
});
