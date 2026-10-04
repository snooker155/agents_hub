import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import SkillSourcesModal from '../SkillSourcesModal';
import { I18nProvider } from '../../../i18n';

const listSkillSources = vi.fn();
const addSkillSource = vi.fn();
const updateSkillSource = vi.fn();
const removeSkillSource = vi.fn();

vi.mock('../../../api/skillVersions', () => ({
  listSkillSources: (...args) => listSkillSources(...args),
  addSkillSource: (...args) => addSkillSource(...args),
  updateSkillSource: (...args) => updateSkillSource(...args),
  removeSkillSource: (...args) => removeSkillSource(...args),
}));

const SOURCES = [
  { id: 'anthropics-skills', repo: 'anthropics/skills', publisher: 'Anthropic', license: 'Apache-2.0',
    kind: 'vendor', note: '', url: 'https://github.com/anthropics/skills', source_id: 'anthropics-skills',
    branch: 'main', updated_at: '2026-10-02T10:00:00Z', skills: 20, flagged: 1 },
  { id: 'getsentry-skills', repo: 'getsentry/skills', publisher: 'Sentry', license: 'Apache-2.0',
    kind: 'vendor', note: '', url: 'https://github.com/getsentry/skills', source_id: null },
];

const show = () => render(
  <I18nProvider>
    <SkillSourcesModal workspace="dev" onClose={() => {}} onChanged={() => {}} />
  </I18nProvider>,
);

beforeEach(() => {
  [listSkillSources, addSkillSource, updateSkillSource, removeSkillSource].forEach((m) => m.mockReset());
  listSkillSources.mockResolvedValue({ data: SOURCES });
  addSkillSource.mockResolvedValue({ data: { source: {}, sync: { added: [{}] }, already_present: false } });
  updateSkillSource.mockResolvedValue({ data: { source: {}, sync: { unchanged: [{}] } } });
  removeSkillSource.mockResolvedValue({ data: { source_id: 'anthropics-skills', removed: [{}, {}] } });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

describe('SkillSourcesModal', () => {
  it('offers Update and Disconnect for a connected source and Connect for the others', async () => {
    show();
    await waitFor(() => screen.getByText('anthropics/skills'));
    expect(screen.getAllByText('Update')).toHaveLength(1);
    expect(screen.getAllByLabelText('Disconnect')).toHaveLength(1);
    // One Connect for the curated source, one for the URL field.
    expect(screen.getAllByText('Connect')).toHaveLength(2);
  });

  it('updates a connected source by its id', async () => {
    show();
    await waitFor(() => screen.getByText('Update'));
    fireEvent.click(screen.getByText('Update'));
    await waitFor(() => expect(updateSkillSource).toHaveBeenCalledWith('dev', 'anthropics-skills'));
  });

  it('disconnects after a confirmation', async () => {
    show();
    await waitFor(() => screen.getByLabelText('Disconnect'));
    fireEvent.click(screen.getByLabelText('Disconnect'));
    await waitFor(() => expect(removeSkillSource).toHaveBeenCalledWith('dev', 'anthropics-skills'));
    expect(window.confirm).toHaveBeenCalled();
  });

  it('connects a curated source by its URL', async () => {
    show();
    await waitFor(() => screen.getByText('getsentry/skills'));
    fireEvent.click(screen.getAllByText('Connect')[0]);
    await waitFor(() => expect(addSkillSource).toHaveBeenCalledWith('dev', 'https://github.com/getsentry/skills', ''));
  });
});
