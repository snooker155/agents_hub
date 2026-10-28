import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import { TimelineArtifactChip } from '../BuildMessage';

const show = (props) => render(
  <MemoryRouter><I18nProvider><TimelineArtifactChip {...props} /></I18nProvider></MemoryRouter>,
);

describe('TimelineArtifactChip: a file step of the Build view', () => {
  it('opens the file by its name and unfolds the diff beside it', () => {
    show({
      entry: { op: 'add', path: 'out/report.md', additions: 2, deletions: 0, diff: '@@ -0,0 +1,2 @@\n+one\n+two' },
      url: '/artifacts?file=file_1',
    });
    const link = screen.getByText('report.md').closest('a');
    expect(link).toHaveAttribute('href', '/artifacts?file=file_1');
    // Plain text, not a link's look.
    expect(screen.getByText('report.md').className).not.toMatch(/indigo|underline/);
    expect(screen.queryByTestId('timeline-artifact-diff')).toBeNull();
    fireEvent.click(screen.getByRole('button', { expanded: false }));
    expect(screen.getByTestId('timeline-artifact-diff')).toHaveTextContent('+two');
  });

  it('has no diff toggle for a change without a diff', () => {
    show({ entry: { op: 'delete', path: 'old.md' } });
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('old.md').className).toContain('line-through');
  });
});
