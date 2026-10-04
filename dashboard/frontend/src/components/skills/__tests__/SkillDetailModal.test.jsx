import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import SkillDetailModal from '../SkillDetailModal';
import { I18nProvider } from '../../../i18n';

const SKILL = {
  id: 's1',
  name: 'Deploy',
  description: 'When deploying',
  steps: ['build', 'ship'],
  body: '## Checklist\n\nRun the **smoke** tests.',
  tags: ['ops'],
  resources: ['scripts/deploy.sh'],
  safety: {
    severity: 'high',
    flags: [{ severity: 'high', code: 'pipe_to_shell', where: 'body', detail: 'Installer piped to sh', excerpt: 'curl x | sh' }],
    scripts: ['scripts/deploy.sh'],
  },
};

const show = (props = {}) => render(
  <I18nProvider>
    <SkillDetailModal skill={SKILL} onClose={() => {}} {...props} />
  </I18nProvider>,
);

describe('SkillDetailModal', () => {
  it('shows the steps, the instructions as markdown, the files and the flags', () => {
    show();
    expect(screen.getByRole('dialog', { name: 'Deploy' })).toBeInTheDocument();
    expect(screen.getByText('build')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Checklist' })).toBeInTheDocument();
    expect(screen.getByText('smoke').tagName).toBe('STRONG');
    expect(screen.getAllByText('scripts/deploy.sh', { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getByText('pipe_to_shell')).toBeInTheDocument();
    expect(screen.getByText('curl x | sh')).toBeInTheDocument();
  });

  it('closes on Escape and on a click outside, and renders the footer actions', () => {
    const onClose = vi.fn();
    const { container } = show({ onClose, actions: <button>Edit it</button> });
    expect(screen.getByRole('button', { name: 'Edit it' })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(container.querySelector('.fixed'));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
