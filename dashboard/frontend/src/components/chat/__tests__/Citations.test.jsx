import { describe, expect, it } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import Citations, { CitedText } from '../Citations';
import { MessageBubble } from '../MessageBubble';

// RAG citations (common/citation_sink.py): the numbered sources listed under
// a reply, and the [n] markers in its text turned into links to them.

const CITES = [
  {
    n: 1, pool_id: 'p1', file_id: 'p1::handbook.md', filename: 'handbook.md', chunk_idx: 0,
    heading_path: ['Handbook', 'Leave'], snippet: 'Every employee gets 30 days of paid leave.',
    score: 0.9, workspace_file_id: 'file_00000000000000aa', layer: 'rag',
  },
  {
    n: 2, pool_id: 'p1', file_id: 'note:Policy', filename: 'Policy', chunk_idx: 0,
    heading_path: null, snippet: 'Unused days carry over.', score: 0.5, workspace_file_id: '', layer: 'note',
  },
];

const wrap = (ui) => render(
  <I18nProvider><MemoryRouter>{ui}</MemoryRouter></I18nProvider>,
);

describe('Citations list', () => {
  it('lists each source with its link: the workspace file, else the memory page', () => {
    wrap(<Citations citations={CITES} anchor="m1" />);
    expect(screen.getByText('Sources')).toBeInTheDocument();
    const file = screen.getByText('handbook.md › Handbook › Leave').closest('a');
    expect(file).toHaveAttribute('href', '/files?file=file_00000000000000aa');
    const note = screen.getByText('Note: Policy').closest('a');
    expect(note).toHaveAttribute('href', '/memory');
    expect(screen.getByText('Every employee gets 30 days of paid leave.')).toBeInTheDocument();
  });

  it('renders nothing without citations', () => {
    const { container } = wrap(<Citations citations={[]} anchor="m1" />);
    expect(container.querySelector('[data-testid="citations"]')).toBeNull();
  });
});

describe('CitedText', () => {
  it('turns known [n] markers into buttons and leaves unknown ones and code alone', () => {
    wrap(<CitedText content={'You get 30 days [1], carried over [2][9].\n```\nx = arr[1]\n```'} citations={CITES} anchor="m1" />);
    const markers = screen.getAllByTestId('citation-marker');
    expect(markers.map((m) => m.textContent)).toEqual(['1', '2']);
    expect(document.body.textContent).toContain('[9]');
    expect(document.body.textContent).toContain('x = arr[1]');
  });

  it('links a grouped marker like [1, 2]', () => {
    wrap(<CitedText content="Both say so [1, 2]." citations={CITES} anchor="m1" />);
    expect(screen.getAllByTestId('citation-marker').map((m) => m.textContent)).toEqual(['1', '2']);
  });

  it('highlights the source a clicked marker points at', () => {
    wrap(<>
      <CitedText content="30 days [1]." citations={CITES} anchor="m1" />
      <Citations citations={CITES} anchor="m1" />
    </>);
    const entry = document.getElementById('m1-cite-1');
    expect(entry.className).not.toContain('ring-1');
    act(() => { fireEvent.click(screen.getByTestId('citation-marker')); });
    expect(entry.className).toContain('ring-1');
  });
});

describe('MessageBubble with citations', () => {
  it('shows the sources under an agent reply', () => {
    wrap(<MessageBubble msg={{ id: 'm2', role: 'agent', content: 'Thirty days [1].', citations: CITES.slice(0, 1) }} />);
    expect(screen.getByTestId('citations')).toBeInTheDocument();
    expect(screen.getByTestId('citation-marker')).toHaveTextContent('1');
  });
});
