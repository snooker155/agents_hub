import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import { ToolPolicySection } from '../WorkspaceSettingsSections';

vi.mock('../ToolPolicySettings', () => ({ default: () => null }));
vi.mock('../LoopSettingsWorkspace', () => ({ default: () => null }));

function state(extra = {}) {
  return {
    workspace: 'team-a',
    policy: { require_tool_approval: false, hooks: {}, ignored_hooks_file: false },
    hooksText: '{}', setHooksText: vi.fn(), hooksError: '', setHooksError: vi.fn(),
    policySaving: false, policySaved: false, toggleApproval: vi.fn(), saveHooks: vi.fn(),
    importHooksFile: vi.fn(),
    ...extra,
  };
}

function renderSection(s) {
  return render(
    <MemoryRouter><I18nProvider><ToolPolicySection s={s} /></I18nProvider></MemoryRouter>,
  );
}

describe('the ignored .hooks.json notice', () => {
  it('is absent when the folder has no hooks file', () => {
    renderSection(state());
    expect(screen.queryByTestId('hooks-file-ignored')).toBeNull();
  });

  it('says the file is not run and imports it on request', () => {
    const s = state({ policy: { require_tool_approval: false, hooks: {}, ignored_hooks_file: true } });
    renderSection(s);
    const notice = screen.getByTestId('hooks-file-ignored');
    expect(notice.textContent).toMatch(/\.hooks\.json/);
    fireEvent.click(notice.querySelector('button'));
    expect(s.importHooksFile).toHaveBeenCalledTimes(1);
  });
});
