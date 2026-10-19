import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../i18n';

vi.mock('../../api', () => ({
  getView: vi.fn(() => Promise.resolve({ data: { view_id: 'v1', kind: 'markdown', title: 'Notes', spec: { markdown: 'hi' } } })),
  applyViewOps: vi.fn(),
  saveViewSnapshot: vi.fn(),
}));
vi.mock('../../api/files', () => ({ saveBlobAs: vi.fn(), uploadWorkspaceFileObject: vi.fn() }));
vi.mock('../viewImage', async (orig) => ({ ...(await orig()), captureViewImage: vi.fn(() => Promise.resolve(new Blob(['x']))) }));

import { saveBlobAs } from '../../api/files';
import { captureViewImage } from '../viewImage';
import ViewCard from '../ViewCard';

describe('ViewCard image export', () => {
  it('offers an Image menu that captures and downloads', async () => {
    render(<I18nProvider><MemoryRouter><ViewCard viewRef={{ view_id: 'v1' }} /></MemoryRouter></I18nProvider>);
    fireEvent.click(await screen.findByTitle('Image'));
    expect(screen.getByRole('menuitem', { name: /Save to workspace files/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: /Download PNG/ }));
    await waitFor(() => expect(saveBlobAs).toHaveBeenCalled());
    expect(captureViewImage).toHaveBeenCalled();
    expect(saveBlobAs.mock.calls[0][1]).toBe('Notes.png');
  });
});
