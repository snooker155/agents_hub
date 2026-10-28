import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../i18n';

vi.mock('../../api', () => ({
  getView: vi.fn((id) => Promise.resolve({ data: { view_id: id, kind: 'markdown', title: 'Notes', spec: { markdown: 'hi' } } })),
  applyViewOps: vi.fn(),
}));

import ViewCard from '../ViewCard';

function withHeight(px) {
  return vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(px);
}

const renderCard = (props) => render(
  <I18nProvider><MemoryRouter><ViewCard {...props} /></MemoryRouter></I18nProvider>,
);

// The box that clips the view: it carries the peek height while folded.
const foldBox = () => screen.getByTestId('view-fold');

describe('ViewCard fold in chat', () => {
  afterEach(() => vi.restoreAllMocks());

  it('shows only the top of a long view', async () => {
    withHeight(2000);
    renderCard({ viewRef: { view_id: 'm1' }, inChat: true });
    await screen.findByText('hi');
    await waitFor(() => expect(foldBox().style.maxHeight).toBe('384px'));
    expect(screen.getByTitle('Expand')).toBeTruthy();
  });

  it('leaves a short view whole', async () => {
    withHeight(300);
    renderCard({ viewRef: { view_id: 'm2' }, inChat: true });
    await screen.findByText('hi');
    expect(foldBox().style.maxHeight).toBe('');
  });

  it('does not fold outside a chat reply', async () => {
    withHeight(2000);
    renderCard({ viewRef: { view_id: 'm3' } });
    await screen.findByText('hi');
    expect(screen.queryByTestId('view-fold')).toBeNull();
  });
});
