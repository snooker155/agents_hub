import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import ToastProvider from '../../../components/ToastProvider';
import NarrativePanel from '../narrative';
import { updateScenario } from '../../../api';

vi.mock('../../../api', () => ({ updateScenario: vi.fn() }));

const SCENARIO = {
  scenario_id: 'scn_1', name: 'The long winter', description: 'A town in debt',
  narrative: '# The town\n\nNobody has paid the guild in three winters.',
  roles: [],
};

const wrap = (ui) => render(
  <I18nProvider><ToastProvider>{ui}</ToastProvider></I18nProvider>,
);

beforeEach(() => vi.clearAllMocks());

describe('NarrativePanel', () => {
  it('opens on what is stored, and renders it beside the editor', () => {
    wrap(<NarrativePanel scenario={SCENARIO} onSaved={() => {}} />);
    expect(screen.getByRole('textbox')).toHaveValue(SCENARIO.narrative);
    // The preview is the same text as Markdown: the heading is a heading.
    expect(screen.getByRole('heading', { name: 'The town' })).toBeInTheDocument();
  });

  it('offers to save only once something changed, and saves the text', async () => {
    updateScenario.mockResolvedValue({ data: { ...SCENARIO, narrative: 'Rewritten.' } });
    const onSaved = vi.fn();
    wrap(<NarrativePanel scenario={SCENARIO} onSaved={onSaved} />);
    expect(screen.queryByText(/Save narrative/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Rewritten.' } });
    fireEvent.click(screen.getByText(/Save narrative/));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(updateScenario).toHaveBeenCalledWith('scn_1', expect.objectContaining({
      scenario_id: 'scn_1', narrative: 'Rewritten.',
    }));
  });

  it('adopts a narrative written elsewhere, but never over unsaved edits', () => {
    const { rerender } = wrap(<NarrativePanel scenario={SCENARIO} onSaved={() => {}} />);
    const box = screen.getByRole('textbox');

    // Nothing unsaved: the chat's version simply arrives.
    rerender(
      <I18nProvider><ToastProvider>
        <NarrativePanel scenario={{ ...SCENARIO, narrative: 'From the chat.' }} onSaved={() => {}} />
      </ToastProvider></I18nProvider>,
    );
    expect(box).toHaveValue('From the chat.');

    // Something unsaved: the typing survives and the conflict is announced.
    fireEvent.change(box, { target: { value: 'Mine.' } });
    rerender(
      <I18nProvider><ToastProvider>
        <NarrativePanel scenario={{ ...SCENARIO, narrative: 'Theirs.' }} onSaved={() => {}} />
      </ToastProvider></I18nProvider>,
    );
    expect(box).toHaveValue('Mine.');
    expect(screen.getByText(/changed/i)).toBeInTheDocument();
  });
});
