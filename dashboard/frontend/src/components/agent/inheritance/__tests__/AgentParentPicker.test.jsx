import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../../../i18n';

const ok = (data) => Promise.resolve({ data });
const getAgentVersions = vi.fn();

vi.mock('../../../../api', () => ({
  getAgentVersions: (...a) => getAgentVersions(...a),
}));

import AgentParentPicker from '../AgentParentPicker';

const AGENTS = [
  { id: 'writer', name: 'Writer', system: true },
  { id: 'analyst', name: 'Analyst', system: true },
  { id: 'my_bot', name: 'My Bot', system: false },
  { id: 'imported', name: 'Imported', remote: { url: 'https://x' } },
  { id: 'self', name: 'Self', system: false },
];

const show = (props = {}) => {
  const onChange = props.onChange || vi.fn();
  const utils = render(
    <I18nProvider>
      <AgentParentPicker
        agents={AGENTS}
        excludeId="self"
        value={{ extends: '', extends_version: null }}
        onChange={onChange}
        idPrefix="test-picker"
        {...props}
      />
    </I18nProvider>,
  );
  return { ...utils, onChange };
};

describe('AgentParentPicker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getAgentVersions.mockImplementation(() => ok({
      versions: [{ version: 1 }, { version: 2 }],
    }));
  });

  it('excludes remote agents and the agent itself, sorting system agents first', () => {
    show();
    const select = screen.getByLabelText('Based on');
    const options = within(select).getAllByRole('option').map((o) => o.textContent);
    expect(options).toContain('Writer');
    expect(options).toContain('Analyst');
    expect(options).toContain('My Bot');
    expect(options).not.toContain('Imported');
    expect(options).not.toContain('Self');
    // System agents (Writer, Analyst) come before the non-system one (My Bot).
    expect(options.indexOf('Writer')).toBeLessThan(options.indexOf('My Bot'));
    expect(options.indexOf('Analyst')).toBeLessThan(options.indexOf('My Bot'));
  });

  it('picking a parent reports it and offers a version pin', async () => {
    const { onChange } = show();
    fireEvent.change(screen.getByLabelText('Based on'), { target: { value: 'writer' } });
    expect(onChange).toHaveBeenCalledWith({ extends: 'writer', extends_version: null });
  });

  it('loads the parent\'s versions once one is chosen and pins to it', async () => {
    const onChange = vi.fn();
    show({ value: { extends: 'writer', extends_version: null }, onChange });
    await waitFor(() => expect(getAgentVersions).toHaveBeenCalledWith('writer'));
    const pin = await screen.findByLabelText('Pin to version');
    fireEvent.change(pin, { target: { value: '2' } });
    expect(onChange).toHaveBeenCalledWith({ extends: 'writer', extends_version: 2 });
  });

  it('shows a pinned badge once a version is set', async () => {
    show({ value: { extends: 'writer', extends_version: 1 } });
    expect(await screen.findByText('pinned')).toBeTruthy();
  });
});
