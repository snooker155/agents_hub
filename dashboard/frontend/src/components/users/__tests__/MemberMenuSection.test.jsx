import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nProvider } from '../../../i18n';
import { AuthContext, DEFAULT_AUTH } from '../../auth';

const getMemberMenu = vi.fn();
const setMemberMenu = vi.fn();
vi.mock('../../../api/memberMenu', () => ({
  getMemberMenu: (...a) => getMemberMenu(...a),
  setMemberMenu: (...a) => setMemberMenu(...a),
}));

import MemberMenuSection from '../MemberMenuSection';

const admin = { ...DEFAULT_AUTH, mode: 'multi', loading: false, user: { username: 'a', role: 'admin' }, features: {} };
const view = () => render(
  <I18nProvider>
    <AuthContext.Provider value={admin}><MemberMenuSection /></AuthContext.Provider>
  </I18nProvider>,
);
const box = (path) => document.querySelector(`input[data-path="${path}"]`);

describe('MemberMenuSection', () => {
  beforeEach(() => {
    getMemberMenu.mockReset();
    setMemberMenu.mockReset();
    getMemberMenu.mockResolvedValue({ data: { hidden: ['/models'], default: ['/models'], is_default: true } });
  });

  it('lists the sidebar rows without chat and account, marking the hidden ones', async () => {
    view();
    await waitFor(() => expect(box('/models')).toBeTruthy());
    expect(box('/models').checked).toBe(true);
    expect(box('/agents').checked).toBe(false);
    expect(box('/chat')).toBeNull();
    expect(box('/account')).toBeNull();
  });

  it('saves the checked rows', async () => {
    setMemberMenu.mockResolvedValue({ data: { hidden: ['/models', '/costs'], default: [], is_default: false } });
    view();
    await waitFor(() => expect(box('/costs')).toBeTruthy());
    fireEvent.click(box('/costs'));
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(setMemberMenu).toHaveBeenCalledWith(['/models', '/costs']));
  });

  it('resets to the default with null and shows a refusal', async () => {
    setMemberMenu.mockRejectedValueOnce({ response: { data: { detail: 'nope' } } });
    view();
    await waitFor(() => expect(box('/models')).toBeTruthy());
    fireEvent.click(screen.getByText('Reset to default'));
    await waitFor(() => expect(setMemberMenu).toHaveBeenCalledWith(null));
    expect(await screen.findByText('nope')).toBeTruthy();
  });
});
