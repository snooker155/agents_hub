import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n';
import ResultBlock from '../task/ResultBlock';

const entry = { agent_id: 'swe_agent', run_id: 'run-12345678', result: 'Styled the page',
  files: ['index.html', 'styles.css'], subtask: { id: 's', key: 'ZS-4', title: 'Style it' } };

describe('ResultBlock files', () => {
  it('lists the files the run changed and opens one', () => {
    const onOpenFile = vi.fn();
    render(<MemoryRouter><I18nProvider><ResultBlock entry={entry} onOpenFile={onOpenFile} /></I18nProvider></MemoryRouter>);
    expect(screen.getByTestId('result-files')).toHaveTextContent('Changed files:');
    fireEvent.click(screen.getByRole('button', { name: /styles\.css/ }));
    expect(onOpenFile).toHaveBeenCalledWith('styles.css');
  });

  it('shows no file row for a run that changed nothing', () => {
    render(<MemoryRouter><I18nProvider><ResultBlock entry={{ ...entry, files: [] }} /></I18nProvider></MemoryRouter>);
    expect(screen.queryByTestId('result-files')).toBeNull();
  });
});
