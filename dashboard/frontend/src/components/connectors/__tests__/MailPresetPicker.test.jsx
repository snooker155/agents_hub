import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import MailPresetPicker from '../MailPresetPicker';
import { presetForAddress } from '../mailPresets';

const t = (k, vars) => (vars ? `${k} ${JSON.stringify(vars)}` : k);
const PRESETS = [
  { id: 'gmail', label: 'Gmail', domains: ['gmail.com'], auth: 'app_password', help_url: 'https://g/help',
    watcher: { host: 'imap.gmail.com', port: 993, ssl: true }, channel: { imap_host: 'imap.gmail.com' } },
  { id: 'outlook', label: 'Outlook', domains: ['outlook.com'], auth: 'oauth', help_url: 'https://m/help',
    watcher: { host: 'outlook.office365.com', port: 993, ssl: true }, channel: { imap_host: 'outlook.office365.com' } },
];

describe('MailPresetPicker', () => {
  it('renders nothing without presets', () => {
    const { container } = render(<MailPresetPicker presets={[]} value="" onPick={() => {}} t={t} inputCls="" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('hands the picked preset to the form and explains its password rule', () => {
    const onPick = vi.fn();
    const { rerender } = render(<MailPresetPicker presets={PRESETS} value="" onPick={onPick} t={t} inputCls="" />);
    fireEvent.change(screen.getByTestId('mail-preset'), { target: { value: 'gmail' } });
    expect(onPick).toHaveBeenCalledWith(PRESETS[0]);
    rerender(<MailPresetPicker presets={PRESETS} value="gmail" onPick={onPick} t={t} inputCls="" />);
    expect(screen.getByTestId('mail-preset-hint')).toHaveTextContent('mailPresets.auth.app_password {"provider":"Gmail"}');
    expect(screen.getByText('mailPresets.help').closest('a')).toHaveAttribute('href', 'https://g/help');
    rerender(<MailPresetPicker presets={PRESETS} value="outlook" onPick={onPick} t={t} inputCls="" />);
    expect(screen.getByTestId('mail-preset-hint')).toHaveTextContent('mailPresets.auth.oauth');
  });

  it('offers the Google sign in for Gmail only, and hides the app password note once it is on', () => {
    const onUseGoogle = vi.fn();
    const { rerender } = render(<MailPresetPicker presets={PRESETS} value="gmail" onPick={() => {}} onUseGoogle={onUseGoogle} t={t} inputCls="" />);
    fireEvent.click(screen.getByTestId('mail-preset-use-google'));
    expect(onUseGoogle).toHaveBeenCalled();
    rerender(<MailPresetPicker presets={PRESETS} value="outlook" onPick={() => {}} onUseGoogle={onUseGoogle} t={t} inputCls="" />);
    expect(screen.queryByTestId('mail-preset-use-google')).toBeNull();
    rerender(<MailPresetPicker presets={PRESETS} value="gmail" onPick={() => {}} onUseGoogle={onUseGoogle} googleActive t={t} inputCls="" />);
    expect(screen.queryByTestId('mail-preset-hint')).toBeNull();
  });

  it('guesses a preset from an address domain', () => {
    expect(presetForAddress(PRESETS, 'Me@Gmail.com')?.id).toBe('gmail');
    expect(presetForAddress(PRESETS, 'me@example.org')).toBeNull();
    expect(presetForAddress(PRESETS, 'me')).toBeNull();
  });
});
