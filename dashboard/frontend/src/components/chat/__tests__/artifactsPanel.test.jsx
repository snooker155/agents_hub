import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// The Artifacts panel: a list of the changed files and made views on the
// left, the selected one on the right as its diff or its content now.

const getWorkspaceFileContent = vi.fn(() => Promise.resolve({ data: { content: 'print("now")' } }));
vi.mock('../../../api', () => ({ getWorkspaceFileContent: (...a) => getWorkspaceFileContent(...a) }));
vi.mock('../../../views/ViewCard', () => ({ default: ({ viewRef }) => <div data-testid="view-card">{viewRef.title}</div> }));
vi.mock('../../GraphMirror', () => ({ default: () => null }));
vi.mock('../../ProcessGraph', () => ({ default: () => null, TokenPill: () => null }));

import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import { ArtifactsPanel } from '../panels';

const ARTIFACTS = {
  'src/app.py': { op: 'modify', path: 'src/app.py', diff: '@@ -1 +1 @@\n-old\n+new', additions: 1, deletions: 1 },
  'src/new.py': { op: 'add', path: 'src/new.py', diff: '+print("now")', additions: 1, deletions: 0 },
};
const VIEWS = [{ view_id: 'v1', title: 'Sales chart' }];

const draw = (props) => render(<I18nProvider><MemoryRouter><ArtifactsPanel {...props} /></MemoryRouter></I18nProvider>);

describe('ArtifactsPanel', () => {
  it('lists files and views and shows the newest view first', () => {
    draw({ artifacts: ARTIFACTS, views: VIEWS, workspace: 'ws' });
    expect(screen.getByText('app.py')).toBeInTheDocument();
    expect(screen.getByText('new.py')).toBeInTheDocument();
    expect(screen.getByTestId('view-card')).toHaveTextContent('Sales chart');
  });

  it('shows each file as it is now by default, deleted files left out', async () => {
    const withDeleted = { ...ARTIFACTS, 'src/old.py': { op: 'delete', path: 'src/old.py', diff: '-gone', additions: 0, deletions: 1 } };
    draw({ artifacts: withDeleted, views: [], workspace: 'ws' });
    expect(screen.queryByText('old.py')).not.toBeInTheDocument();
    // The first file (by path) opens on its content.
    await waitFor(() => expect(getWorkspaceFileContent).toHaveBeenCalledWith('ws', 'src/app.py'));
    await waitFor(() => expect(screen.getByTestId('artifact-detail')).toHaveTextContent('print("now")'));
    expect(screen.getByTestId('artifact-detail').querySelector('.hl-code-block__lang')).toHaveTextContent('python');
    expect(screen.getByTestId('artifact-detail')).not.toHaveTextContent('-old');
  });

  it('shows what changed in diff mode, deleted files included', () => {
    const withDeleted = { ...ARTIFACTS, 'src/old.py': { op: 'delete', path: 'src/old.py', diff: '-gone', additions: 0, deletions: 1 } };
    draw({ artifacts: withDeleted, views: [], workspace: 'ws', mode: 'diff' });
    expect(screen.getByText('old.py')).toBeInTheDocument();
    expect(screen.getByTestId('artifact-detail')).toHaveTextContent('+new');
    expect(screen.getByTestId('artifact-detail')).toHaveTextContent('-old');
    fireEvent.click(screen.getByText('old.py'));
    expect(screen.getByTestId('artifact-detail')).toHaveTextContent('-gone');
  });

  it('says so when there is nothing yet', () => {
    draw({ artifacts: {}, views: [] });
    expect(screen.getByText(/No files or views yet/)).toBeInTheDocument();
  });
});
