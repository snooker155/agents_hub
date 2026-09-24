import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

import { I18nProvider } from '../../i18n';
import ToastProvider from '../../components/ToastProvider';
import SystemPatchCard from '../../components/SystemPatchCard';

// TaskDetails.jsx (1800+ lines) pulls in the workspace and stream contexts,
// ProcessGraph, LiveRunStream's own EventSource, DateInput and InlineEdit,
// plus ~30 endpoints from api/index.js — mocking that whole surface just to
// exercise the system-patch card is disproportionate to what changed there
// (ResultBlock now renders <SystemPatchCard> above the markdown when
// parseSystemPatch finds the marker; that wiring is two lines, see
// src/pages/TaskDetails.jsx's ResultBlock). So this file tests SystemPatchCard
// directly, with the real I18nProvider/ToastProvider it renders under in the
// app, which covers the same rendering and copy behaviour without the rest of
// the page's weight. parseSystemPatch itself is covered by
// src/components/__tests__/systemPatch.test.js.

const META = {
  branch: 'system/2026-09-24-fix-doctor',
  repo_dir: '/srv/agents_hub',
  commit: 'a1b2c3d4e5f',
  fetch_command: 'git fetch origin system/2026-09-24-fix-doctor',
};

const show = (meta = META) => render(
  <I18nProvider><ToastProvider><SystemPatchCard meta={meta} /></ToastProvider></I18nProvider>,
);

describe('SystemPatchCard', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete navigator.clipboard;
  });

  it('shows the branch and a shortened commit', () => {
    show();
    expect(screen.getByText('system/2026-09-24-fix-doctor')).toBeInTheDocument();
    expect(screen.getByText('a1b2c3d')).toBeInTheDocument();
  });

  it('puts the fetch command in a readonly box', () => {
    show();
    const box = screen.getByDisplayValue('git fetch origin system/2026-09-24-fix-doctor');
    expect(box).toHaveAttribute('readonly');
  });

  it('names the human step explicitly', () => {
    show();
    expect(screen.getByText(/pull request/i)).toBeInTheDocument();
  });

  it('copies the fetch command and confirms it when the clipboard works', async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.assign(navigator, { clipboard: { writeText } });
    show();
    fireEvent.click(screen.getByText(/copy command/i));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(META.fetch_command));
    await waitFor(() => expect(screen.getByText(/copied/i)).toBeInTheDocument());
  });

  it('falls back to selecting the textarea when the clipboard is unavailable', async () => {
    delete navigator.clipboard;
    show();
    const box = screen.getByDisplayValue(META.fetch_command);
    const selectSpy = vi.spyOn(box, 'select');
    fireEvent.click(screen.getByText(/copy command/i));
    await waitFor(() => expect(selectSpy).toHaveBeenCalled());
    expect(document.activeElement).toBe(box);
  });
});
