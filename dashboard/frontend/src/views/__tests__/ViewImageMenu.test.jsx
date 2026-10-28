import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { ToastContext } from '../../components/toast';

vi.mock('../viewImage', async (orig) => ({ ...(await orig()), captureViewImage: vi.fn(() => Promise.resolve(new Blob(['png']))) }));
vi.mock('../../api/files', () => ({
  saveBlobAs: vi.fn(),
  uploadWorkspaceFileObject: vi.fn(() => Promise.resolve({ data: { file_id: 'f_1' } })),
}));

import { captureViewImage } from '../viewImage';
import { saveBlobAs, uploadWorkspaceFileObject } from '../../api/files';
import ViewImageMenu from '../ViewImageMenu';

const toast = { error: vi.fn(), success: vi.fn(), dismiss: vi.fn() };
const view = { view_id: 'v1', kind: 'chart', title: 'Sales / Q3', workspace: 'ws1' };
const show = () => render(
  <I18nProvider><ToastContext.Provider value={toast}>
    <ViewImageMenu view={view} getRoot={() => document.body} />
  </ToastContext.Provider></I18nProvider>,
);

describe('ViewImageMenu', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('downloads the PNG under a sanitised name', async () => {
    show();
    fireEvent.click(screen.getByRole('button'));
    fireEvent.click(screen.getByRole('menuitem', { name: /Download PNG/ }));
    await waitFor(() => expect(saveBlobAs).toHaveBeenCalled());
    expect(captureViewImage).toHaveBeenCalledWith(document.body, { kind: 'chart' });
    expect(saveBlobAs.mock.calls[0][1]).toBe('Sales Q3.png');
  });

  it('saves to the view workspace files and toasts', async () => {
    show();
    fireEvent.click(screen.getByRole('button'));
    fireEvent.click(screen.getByRole('menuitem', { name: /Save to workspace files/ }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(uploadWorkspaceFileObject.mock.calls[0][0]).toBe('ws1');
    expect(uploadWorkspaceFileObject.mock.calls[0][2]).toMatchObject({ filename: 'Sales Q3.png' });
  });

  it('shows a message when the view cannot be captured', async () => {
    captureViewImage.mockRejectedValueOnce(Object.assign(new Error('iframe'), { code: 'iframe' }));
    show();
    fireEvent.click(screen.getByRole('button'));
    fireEvent.click(screen.getByRole('menuitem', { name: /Download PNG/ }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error.mock.calls[0][0]).toMatch(/isolated frame/);
    expect(saveBlobAs).not.toHaveBeenCalled();
  });
});
