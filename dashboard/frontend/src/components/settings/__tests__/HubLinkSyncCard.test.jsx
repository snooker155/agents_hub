import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const sync = vi.hoisted(() => ({
  getHubSync: vi.fn(), saveHubSync: vi.fn(), setHubSyncKey: vi.fn(), forgetHubSyncKey: vi.fn(),
  runHubSync: vi.fn(), listHubSyncConflicts: vi.fn(), getHubSyncConflict: vi.fn(),
  resolveHubSyncConflict: vi.fn(), relativeTime: () => 'a minute ago', notifySyncChanged: () => {},
}));
vi.mock('../../../api/hubSync', () => sync);
vi.mock('../../../api/workspaces', () => ({ getWorkspaces: () => Promise.resolve({ data: [{ name: 'default' }, { name: 'work' }] }) }));
vi.mock('../../../i18n', () => ({ useI18n: () => ({ t: (k) => k, language: 'en' }) }));

import HubLinkSyncCard from '../HubLinkSyncCard';

const base = {
  linked: true, enabled: false, encrypted: false, key_set: false, key_unlocked: false,
  mappings: [{ workspace: 'default', space: 'default' }], last_pass_at: null, last_result: {},
  last_error: null, conflicts: 0,
};

describe('HubLinkSyncCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // clearAllMocks keeps implementations, and one test leaves a request pending.
    sync.setHubSyncKey.mockReset();
    sync.getHubSync.mockResolvedValue({ data: base });
    sync.saveHubSync.mockResolvedValue({ data: base });
    sync.listHubSyncConflicts.mockResolvedValue({ data: { conflicts: [] } });
  });

  it('renders mappings and saves them', async () => {
    render(<HubLinkSyncCard />);
    await waitFor(() => expect(screen.getByTestId('sync-mapping')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('sync-add-mapping'));
    const spaces = screen.getAllByTestId('sync-mapping-space');
    expect(spaces).toHaveLength(2);
    expect(spaces[1].value).toBe('work');
    fireEvent.click(screen.getByTestId('sync-enabled'));
    fireEvent.click(screen.getByTestId('sync-save'));
    await waitFor(() => expect(sync.saveHubSync).toHaveBeenCalledWith({
      enabled: true, encrypt_all: false,
      mappings: [{ workspace: 'default', space: 'default', encrypted: false }, { workspace: 'work', space: 'work', encrypted: false }],
    }));
  });

  it('shows a generated passphrase and requires the checkbox', async () => {
    sync.setHubSyncKey.mockResolvedValue({ data: { generated_passphrase: 'alpha-beta-gamma' } });
    render(<HubLinkSyncCard />);
    await waitFor(() => expect(screen.getByTestId('sync-mapping-encrypted')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('sync-mapping-encrypted'));
    fireEvent.click(screen.getByTestId('sync-key-generate'));
    await waitFor(() => expect(screen.getByTestId('sync-passphrase')).toHaveTextContent('alpha-beta-gamma'));
    expect(sync.setHubSyncKey).toHaveBeenCalledWith({ generate: true });
    const done = screen.getByTestId('sync-key-done');
    expect(done).toBeDisabled();
    fireEvent.click(screen.getByTestId('sync-passphrase-stored'));
    expect(done).not.toBeDisabled();
    fireEvent.click(done);
    await waitFor(() => expect(screen.queryByTestId('sync-passphrase')).toBeNull());
    expect(screen.getByTestId('sync-mapping-encrypted')).toBeChecked();
  });

  it('reports a wrong passphrase on a second device', async () => {
    sync.setHubSyncKey.mockRejectedValue({ response: { status: 403, data: { detail: 'no' } } });
    render(<HubLinkSyncCard />);
    await waitFor(() => expect(screen.getByTestId('sync-mapping-encrypted')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('sync-mapping-encrypted'));
    fireEvent.click(screen.getByTestId('sync-key-have'));
    fireEvent.change(screen.getByTestId('sync-key-input'), { target: { value: 'bad' } });
    fireEvent.click(screen.getByTestId('sync-key-use'));
    await waitFor(() => expect(screen.getByText('hubSync.keyDialog.wrong')).toBeInTheDocument());
  });

  it('saves encryption per workspace row', async () => {
    sync.getHubSync.mockResolvedValue({ data: { ...base, key_set: true, key_unlocked: true, mappings: [
      { workspace: 'default', space: 'default', encrypted: true }, { workspace: 'work', space: 'w', encrypted: false }] } });
    render(<HubLinkSyncCard />);
    await waitFor(() => expect(screen.getAllByTestId('sync-mapping-encrypted')).toHaveLength(2));
    const boxes = screen.getAllByTestId('sync-mapping-encrypted');
    expect(boxes[0]).toBeChecked();
    expect(boxes[1]).not.toBeChecked();
    fireEvent.click(boxes[1]);
    fireEvent.click(boxes[0]);
    fireEvent.click(screen.getByTestId('sync-save'));
    await waitFor(() => expect(sync.saveHubSync).toHaveBeenCalledWith({
      enabled: false, encrypt_all: false,
      mappings: [{ workspace: 'default', space: 'default', encrypted: false }, { workspace: 'work', space: 'w', encrypted: true }],
    }));
  });

  it('with every workspace encrypted, ticks and locks each row and saves it so', async () => {
    sync.getHubSync.mockResolvedValue({ data: { ...base, key_set: true, key_unlocked: true, encrypt_all: true, mappings: [
      { workspace: 'default', space: 'default', encrypted: true }] } });
    render(<HubLinkSyncCard />);
    await waitFor(() => expect(screen.getByTestId('sync-encrypt-all')).toBeChecked());
    expect(screen.getByText('hubSync.encryption.allHint')).toBeInTheDocument();
    const row = screen.getByTestId('sync-mapping-encrypted');
    expect(row).toBeChecked();
    expect(row).toBeDisabled();
    fireEvent.click(screen.getByTestId('sync-add-mapping'));
    fireEvent.click(screen.getByTestId('sync-save'));
    await waitFor(() => expect(sync.saveHubSync).toHaveBeenCalledWith({
      enabled: false, encrypt_all: true,
      mappings: [{ workspace: 'default', space: 'default', encrypted: true }, { workspace: 'work', space: 'work', encrypted: true }],
    }));
    await waitFor(() => expect(screen.getByText('hubSync.card.saved')).toBeInTheDocument());
    // The reload after a save settles before the next change.
    await waitFor(() => expect(sync.getHubSync).toHaveBeenCalledTimes(2));
    // Off again: the rows are free to choose.
    fireEvent.click(screen.getByTestId('sync-encrypt-all'));
    await waitFor(() => expect(screen.getByTestId('sync-encrypt-all')).not.toBeChecked());
    expect(screen.getAllByTestId('sync-mapping-encrypted')[0]).not.toBeDisabled();
  });

  it('is there before the hub is linked, pointing at the link form', async () => {
    sync.getHubSync.mockResolvedValue({ data: { linked: false, enabled: false } });
    render(<HubLinkSyncCard />);
    await waitFor(() => expect(screen.getByTestId('sync-card-unlinked')).toBeInTheDocument());
    expect(screen.getByText('hubSync.card.notLinked')).toBeInTheDocument();
  });

  it('lists a conflict and shows the other version on demand', async () => {
    sync.getHubSync.mockResolvedValue({ data: { ...base, conflicts: 1 } });
    sync.listHubSyncConflicts.mockResolvedValue({ data: { conflicts: [{ conflict_id: 'c1', kind: 'agent', record_id: 'r1', kept: 'local' }] } });
    sync.getHubSyncConflict.mockResolvedValue({ data: { lost_doc: { name: 'old' } } });
    render(<HubLinkSyncCard />);
    await waitFor(() => expect(screen.getByTestId('sync-conflict')).toBeInTheDocument());
    fireEvent.click(screen.getByText('hubSync.conflicts.show'));
    await waitFor(() => expect(screen.getByTestId('sync-conflict-doc')).toHaveTextContent('"name": "old"'));
  });

  const openGenerate = async () => {
    render(<HubLinkSyncCard />);
    await waitFor(() => expect(screen.getByTestId('sync-mapping-encrypted')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('sync-mapping-encrypted'));
    fireEvent.click(screen.getByTestId('sync-key-generate'));
  };

  it('cannot be closed while a generated passphrase is not yet stored', async () => {
    sync.setHubSyncKey.mockResolvedValue({ data: { generated_passphrase: 'alpha-beta-gamma' } });
    await openGenerate();
    await waitFor(() => expect(screen.getByTestId('sync-passphrase')).toBeInTheDocument());
    const x = screen.getByTestId('sync-key-close');
    expect(x).toBeDisabled();
    fireEvent.click(x);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByTestId('sync-passphrase')).toHaveTextContent('alpha-beta-gamma');

    // Once stored, closing counts as done: encryption stays ticked, as after Continue.
    fireEvent.click(screen.getByTestId('sync-passphrase-stored'));
    expect(x).not.toBeDisabled();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('sync-key-dialog')).toBeNull();
    expect(screen.getByTestId('sync-mapping-encrypted')).toBeChecked();
  });

  it('cannot be closed while the generate request is still out', async () => {
    let answer;
    sync.setHubSyncKey.mockImplementation(() => new Promise((r) => { answer = r; }));
    await openGenerate();
    expect(screen.getByTestId('sync-key-close')).toBeDisabled();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByTestId('sync-key-dialog')).toBeInTheDocument();
    answer({ data: { generated_passphrase: 'late-but-shown' } });
    await waitFor(() => expect(screen.getByTestId('sync-passphrase')).toHaveTextContent('late-but-shown'));
  });

  it('closes freely before anything is generated', async () => {
    render(<HubLinkSyncCard />);
    await waitFor(() => expect(screen.getByTestId('sync-mapping-encrypted')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('sync-mapping-encrypted'));
    fireEvent.click(screen.getByTestId('sync-key-close'));
    expect(screen.queryByTestId('sync-key-dialog')).toBeNull();
  });

  const keyExists = (detail) => ({ response: { status: 409, data: { detail: { code: 'key_exists', ...detail } } } });

  it('offers to start over only when the server says the key is unused', async () => {
    const message = 'this account has a sync key that nothing is encrypted with yet';
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    sync.setHubSyncKey
      .mockRejectedValueOnce(keyExists({ message, replace_unused: true }))
      .mockResolvedValueOnce({ data: { generated_passphrase: 'fresh-new-words' } });
    await openGenerate();
    await waitFor(() => expect(screen.getByText(message)).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('sync-key-start-over'));
    expect(confirm).toHaveBeenCalledWith('hubSync.keyDialog.startOverConfirm');
    expect(sync.setHubSyncKey).toHaveBeenLastCalledWith({ generate: true, replace_unused: true });
    await waitFor(() => expect(screen.getByTestId('sync-passphrase')).toHaveTextContent('fresh-new-words'));
    confirm.mockRestore();
  });

  it('keeps the old key when the start over is not confirmed', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    sync.setHubSyncKey.mockRejectedValueOnce(keyExists({ message: 'unused key', replace_unused: true }));
    await openGenerate();
    fireEvent.click(await screen.findByTestId('sync-key-start-over'));
    expect(sync.setHubSyncKey).toHaveBeenCalledTimes(1);
    expect(screen.getByText('unused key')).toBeInTheDocument();
    confirm.mockRestore();
  });

  it('shows the key_exists message without the offer for a key in use', async () => {
    sync.setHubSyncKey.mockRejectedValue(keyExists({ message: 'records are sealed with it', replace_unused: false }));
    await openGenerate();
    await waitFor(() => expect(screen.getByText('records are sealed with it')).toBeInTheDocument());
    expect(screen.queryByTestId('sync-key-start-over')).toBeNull();
  });

  it('does not take replace_unused from a 409 that is not key_exists', async () => {
    sync.setHubSyncKey.mockRejectedValue({ response: { status: 409, data: { detail: { message: 'other', replace_unused: true } } } });
    await openGenerate();
    await waitFor(() => expect(screen.getByText('other')).toBeInTheDocument());
    expect(screen.queryByTestId('sync-key-start-over')).toBeNull();
  });

  it('ignores the backdrop while the passphrase is not stored, and closes on it after', async () => {
    sync.setHubSyncKey.mockResolvedValue({ data: { generated_passphrase: 'alpha-beta-gamma' } });
    await openGenerate();
    await screen.findByTestId('sync-passphrase');
    fireEvent.click(screen.getByTestId('sync-key-dialog'));
    expect(screen.getByTestId('sync-passphrase')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('sync-passphrase-stored'));
    fireEvent.click(screen.getByTestId('sync-key-dialog'));
    expect(screen.queryByTestId('sync-key-dialog')).toBeNull();
  });

  it('asks before a reload or an in-app link while the passphrase is not stored', async () => {
    sync.setHubSyncKey.mockResolvedValue({ data: { generated_passphrase: 'alpha-beta-gamma' } });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    // A hash link: jsdom follows it without a navigation warning, and nothing
    // but the guard can mark its click prevented.
    render(<><a href="#agents" data-testid="shell-link">agents</a><HubLinkSyncCard /></>);
    await waitFor(() => expect(screen.getByTestId('sync-mapping-encrypted')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('sync-mapping-encrypted'));
    fireEvent.click(screen.getByTestId('sync-key-generate'));
    await screen.findByTestId('sync-passphrase');

    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    const link = screen.getByTestId('shell-link');
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    link.dispatchEvent(click);
    expect(confirm).toHaveBeenCalledWith('hubSync.keyDialog.leaveConfirm');
    expect(click.defaultPrevented).toBe(true);

    // Ticked: both ways out are free again, without a prompt.
    fireEvent.click(screen.getByTestId('sync-passphrase-stored'));
    confirm.mockClear();
    const unload2 = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload2);
    expect(unload2.defaultPrevented).toBe(false);
    const free = new MouseEvent('click', { bubbles: true, cancelable: true });
    link.dispatchEvent(free);
    expect(confirm).not.toHaveBeenCalled();
    expect(free.defaultPrevented).toBe(false);
    confirm.mockRestore();
  });

  it('does not offer to start over for a key that is in use', async () => {
    const message = 'this account already has a sync key: type its passphrase to unlock it';
    sync.setHubSyncKey.mockRejectedValue({ response: { status: 409, data: { detail: message } } });
    await openGenerate();
    await waitFor(() => expect(screen.getByText(message)).toBeInTheDocument());
    expect(screen.queryByTestId('sync-key-start-over')).toBeNull();
  });

  it('says a pass failed when only has_error comes back', async () => {
    sync.getHubSync.mockResolvedValue({ data: { ...base, last_error: '', has_error: true } });
    render(<HubLinkSyncCard />);
    expect(await screen.findByTestId('sync-error-hidden')).toHaveTextContent('hubSync.status.errorHidden');
  });
});
