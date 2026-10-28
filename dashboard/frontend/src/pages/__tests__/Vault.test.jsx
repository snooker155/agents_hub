import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  listSyncSpaces: vi.fn(), listSyncKeys: vi.fn(), listSyncRecords: vi.fn(), getSyncRecord: vi.fn(),
}));
const crypto = vi.hoisted(() => ({ decodeRecord: vi.fn() }));
vi.mock('../../api/syncVault', () => api);
vi.mock('../../lib/syncCrypto', () => crypto);
vi.mock('../../i18n', () => ({ useI18n: () => ({ t: (k) => k, language: 'en' }) }));
// The real dialog derives a key with PBKDF2; here unlocking just hands one over.
vi.mock('../../components/vault/UnlockDialog', () => ({
  default: ({ onUnlocked }) => <button type="button" onClick={() => onUnlocked({ key: 'K' })}>do-unlock</button>,
}));

import Vault from '../Vault';

const SPACES = [
  { space: 'a', encrypted: true, key_id: 'k1', counts: { total: 1 } },
  { space: 'b', encrypted: true, key_id: 'k1', counts: { total: 0 } },
];
const REC = { kind: 'note', record_id: 'r1', encrypted: true, modified_at: null, size: 10 };

// Under a loaded parallel run the decode chain (spaces, records, unlock,
// titles) can take longer than the 1000 ms findBy default; a longer ceiling
// costs nothing when the page is quick, since the wait ends at the first match.
// It stays under the 5000 ms test timeout so a real miss reports the query.
const WAIT = { timeout: 3000 };

const deferred = () => {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
};

async function unlockAndStartOpening() {
  render(<MemoryRouter initialEntries={['/vault?space=a']}><Vault /></MemoryRouter>);
  fireEvent.click(await screen.findByText('vault.unlockToView', {}, WAIT));
  fireEvent.click(screen.getByText('do-unlock'));
  // Titles decode first; then the click on the record waits on a slow fetch.
  await screen.findByText('first title', {}, WAIT);
  const slow = deferred();
  crypto.decodeRecord.mockImplementationOnce(() => slow.promise);
  fireEvent.click(screen.getByText('first title'));
  await waitFor(() => expect(crypto.decodeRecord).toHaveBeenCalledTimes(2), WAIT);
  return slow;
}

describe('Vault', () => {
  beforeEach(() => {
    // Reset, not clear: a once-implementation a test left unused would
    // otherwise answer the next test's first call.
    vi.resetAllMocks();
    api.listSyncSpaces.mockResolvedValue({ data: { spaces: SPACES } });
    api.listSyncKeys.mockResolvedValue({ data: { keys: [{ key_id: 'k1', salt: 's', iterations: 1, verifier: 'v' }] } });
    api.listSyncRecords.mockImplementation((space) => Promise.resolve({ data: { records: space === 'a' ? [REC] : [] } }));
    api.getSyncRecord.mockResolvedValue({ data: { payload: 'x', encrypted: true } });
    crypto.decodeRecord.mockResolvedValueOnce({ kind: 'note', title: 'first title', data: { body: 'x' } });
  });

  it('drops a record that finishes decrypting after Lock', async () => {
    const slow = await unlockAndStartOpening();
    fireEvent.click(screen.getByText('vault.lock'));
    await act(async () => {
      slow.resolve({ kind: 'plan', title: 'SECRET TITLE', data: { goal: 'SECRET BODY' } });
      await slow.promise;
    });
    expect(screen.queryByText('SECRET TITLE')).toBeNull();
    expect(screen.queryByText('SECRET BODY')).toBeNull();
    expect(screen.getByText('vault.unlockToView')).toBeInTheDocument();
  });

  it('drops a record from the old space after switching spaces', async () => {
    const slow = await unlockAndStartOpening();
    fireEvent.click(screen.getByText('b'));
    await screen.findByText('vault.noRecords', {}, WAIT);
    await act(async () => {
      slow.resolve({ kind: 'plan', title: 'SECRET TITLE', data: { goal: 'SECRET BODY' } });
      await slow.promise;
    });
    expect(screen.queryByText('SECRET BODY')).toBeNull();
    expect(screen.getByText('vault.noRecords')).toBeInTheDocument();
  });

  it('drops a record whose fetch was still running when Back was pressed', async () => {
    const slow = await unlockAndStartOpening();
    fireEvent.click(screen.getByText('vault.back'));
    await act(async () => {
      slow.resolve({ kind: 'plan', title: 'SECRET TITLE', data: { goal: 'SECRET BODY' } });
      await slow.promise;
    });
    expect(screen.queryByText('SECRET BODY')).toBeNull();
    expect(screen.getByText('first title')).toBeInTheDocument();
  });

  it('decodes the titles of a list that lands after the unlock', async () => {
    const list = deferred();
    // Every call: the page lists once before the spaces load and again after.
    api.listSyncRecords.mockImplementation(() => list.promise);
    render(<MemoryRouter initialEntries={['/vault?space=a']}><Vault /></MemoryRouter>);
    fireEvent.click(await screen.findByText('vault.unlockToView', {}, WAIT));
    fireEvent.click(screen.getByText('do-unlock'));
    expect(crypto.decodeRecord).not.toHaveBeenCalled();
    await act(async () => {
      list.resolve({ data: { records: [REC] } });
      await list.promise;
    });
    expect(await screen.findByText('first title', {}, WAIT)).toBeInTheDocument();
    expect(crypto.decodeRecord).toHaveBeenCalledWith({ payload: 'x', encrypted: true }, 'K', { space: 'a' });
  });

  it('checks each record against the space the page asked for, not the echoed one', async () => {
    api.getSyncRecord.mockResolvedValue({ data: { payload: 'x', encrypted: true, space: 'b' } });
    const slow = await unlockAndStartOpening();
    await act(async () => {
      slow.resolve({ kind: 'plan', title: 'Plan title', data: { goal: 'ship it' } });
      await slow.promise;
    });
    expect(crypto.decodeRecord).toHaveBeenCalledTimes(2);
    for (const call of crypto.decodeRecord.mock.calls) {
      expect(call[1]).toBe('K');
      expect(call[2]).toEqual({ space: 'a' });
    }
  });

  it('shows the record when nothing interrupts it', async () => {
    const slow = await unlockAndStartOpening();
    await act(async () => {
      slow.resolve({ kind: 'plan', title: 'Plan title', data: { goal: 'ship it' } });
      await slow.promise;
    });
    expect(screen.getByText('ship it')).toBeInTheDocument();
  });
});
