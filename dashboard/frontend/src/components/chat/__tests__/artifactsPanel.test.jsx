import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// The Artifacts panel: a list of the changed files and made views on the
// left, the selected one on the right as its diff or its content now.

const getWorkspaceFileContent = vi.fn(() => Promise.resolve({ data: { content: 'print("now")' } }));
vi.mock('../../../api', () => ({ getWorkspaceFileContent: (...a) => getWorkspaceFileContent(...a) }));
vi.mock('../../../views/ViewCard', () => ({ default: ({ viewRef, actions }) => <div data-testid="view-card">{viewRef.title}{actions}</div> }));
vi.mock('../../GraphMirror', () => ({ default: () => null }));
vi.mock('../../ProcessGraph', () => ({ default: () => null, TokenPill: () => null }));
// The file's editor (components/code/FileWorkbench.jsx) stands in: what it was opened with.
vi.mock('../../code/FileWorkbench', () => ({
  default: ({ workspace, path }) => (
    <div data-testid="file-workbench">{workspace}:{path}</div>
  ),
}));

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
  it('reads an open file smaller or larger from its header, and keeps the size', () => {
    localStorage.removeItem('agent_hub_artifact_font_scale');
    const { unmount } = draw({ artifacts: ARTIFACTS, views: [], workspace: 'ws', mode: 'diff' });
    const scale = () => screen.getByTestId('artifact-detail').firstElementChild.dataset.fontScale;
    expect(scale()).toBe('1');
    fireEvent.click(screen.getByRole('button', { name: /larger text/i }));
    fireEvent.click(screen.getByRole('button', { name: /larger text/i }));
    expect(scale()).toBe('1.25');
    fireEvent.click(screen.getByRole('button', { name: /smaller text/i }));
    expect(scale()).toBe('1.1');
    unmount();
    draw({ artifacts: ARTIFACTS, views: [], workspace: 'ws', mode: 'diff' });
    expect(scale()).toBe('1.1');
    localStorage.removeItem('agent_hub_artifact_font_scale');
  });

  it('on the main chat shows the list alone below the window width it is given', () => {
    // The panel itself is wide enough (jsdom measures 0 without ResizeObserver);
    // the window is 1440px, below the 2300 the chat asks for.
    const width = window.innerWidth;
    window.innerWidth = 1440;
    try {
      draw({ artifacts: ARTIFACTS, views: VIEWS, workspace: 'ws', sideBySideFrom: 2300 });
      expect(screen.getByTestId('artifacts-box')).toHaveAttribute('data-stacked', 'true');
      expect(screen.queryByTestId('view-card')).not.toBeInTheDocument();
    } finally {
      window.innerWidth = width;
    }
  });

  it('lists files and views and shows the newest view first', () => {
    draw({ artifacts: ARTIFACTS, views: VIEWS, workspace: 'ws' });
    expect(screen.getByText('app.py')).toBeInTheDocument();
    expect(screen.getByText('new.py')).toBeInTheDocument();
    expect(screen.getByTestId('view-card')).toHaveTextContent('Sales chart');
  });

  it('shows the file the page asks for (a file clicked in the chat)', () => {
    draw({ artifacts: ARTIFACTS, views: VIEWS, workspace: 'ws', pick: { key: 'file:src/new.py', nonce: 1 } });
    expect(screen.queryByTestId('view-card')).not.toBeInTheDocument();
    expect(document.querySelector('[data-artifact-path="src/new.py"]').className).toContain('bg-indigo-50');
  });

  it('links a view to its panel under Artifacts', () => {
    draw({ artifacts: {}, views: VIEWS, workspace: 'ws' });
    expect(screen.getByTestId('artifact-open')).toHaveAttribute('href', '/artifacts?folder=__views__&view=v1');
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

  it('leaves out saved tool outputs in either mode', () => {
    const withSpill = { ...ARTIFACTS,
      'tool-outputs/r1/001-web_fetch.txt': { op: 'add', path: 'tool-outputs/r1/001-web_fetch.txt', diff: '+long', additions: 1, deletions: 0 } };
    const { unmount } = draw({ artifacts: withSpill, views: [], workspace: 'ws' });
    expect(screen.queryByText('001-web_fetch.txt')).not.toBeInTheDocument();
    unmount();
    draw({ artifacts: withSpill, views: [], workspace: 'ws', mode: 'diff' });
    expect(screen.queryByText('001-web_fetch.txt')).not.toBeInTheDocument();
    expect(screen.getByText('app.py')).toBeInTheDocument();
  });

  it('says so when there is nothing yet', () => {
    draw({ artifacts: {}, views: [] });
    expect(screen.getByText(/No files or views yet/)).toBeInTheDocument();
  });

  it('opens a code file in the editor, which alone has Run', () => {
    draw({ artifacts: ARTIFACTS, views: [], workspace: 'ws', pick: { key: 'file:src/new.py', nonce: 1 } });
    fireEvent.click(screen.getByTestId('artifact-editor'));
    expect(screen.getByTestId('file-workbench')).toHaveTextContent('ws:src/new.py');
    expect(screen.queryByTestId('artifact-run')).not.toBeInTheDocument();
    expect(screen.queryByTestId('artifact-detail')).not.toBeInTheDocument();
    // Editor again: back to the file as it is.
    fireEvent.click(screen.getByTestId('artifact-editor'));
    expect(screen.queryByTestId('file-workbench')).not.toBeInTheDocument();
  });

  it('offers no editor on a Markdown file', () => {
    const files = {
      'README.md': { op: 'add', path: 'README.md', diff: '+# r', additions: 1, deletions: 0 },
      'src/a.ts': { op: 'add', path: 'src/a.ts', diff: '+1', additions: 1, deletions: 0 },
    };
    draw({ artifacts: files, views: [], workspace: 'ws', pick: { key: 'file:README.md', nonce: 1 } });
    expect(screen.queryByTestId('artifact-editor')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('a.ts'));
    expect(screen.getByTestId('artifact-editor')).toBeInTheDocument();
  });

  it('offers no editor in diff mode', () => {
    draw({ artifacts: ARTIFACTS, views: [], workspace: 'ws', mode: 'diff' });
    expect(screen.queryByTestId('artifact-editor')).not.toBeInTheDocument();
  });

  it('in a narrow panel shows the list alone, and an item over the whole panel', () => {
    // The panel measures itself: 400px is narrower than list + item side by side.
    const RO = globalThis.ResizeObserver;
    const rect = Element.prototype.getBoundingClientRect;
    globalThis.ResizeObserver = class { observe() {} disconnect() {} };
    Element.prototype.getBoundingClientRect = function box() { return { width: 400, height: 600 }; };
    try {
      draw({ artifacts: ARTIFACTS, views: VIEWS, workspace: 'ws' });
      expect(screen.getByTestId('artifacts-box')).toHaveAttribute('data-stacked', 'true');
      expect(screen.getByText('app.py')).toBeInTheDocument();
      expect(screen.queryByTestId('view-card')).not.toBeInTheDocument();
      fireEvent.click(screen.getByText('Sales chart'));
      expect(screen.getByTestId('view-card')).toBeInTheDocument();
      expect(screen.queryByText('app.py')).not.toBeInTheDocument();
      fireEvent.click(screen.getByTestId('artifacts-back'));
      expect(screen.getByText('app.py')).toBeInTheDocument();
      expect(screen.queryByTestId('view-card')).not.toBeInTheDocument();
      // Files are blocks with the start of each file: a new one from its
      // diff, a changed one read from the workspace (the mock answers print("now")).
      const cards = screen.getAllByTestId('artifact-card');
      expect(cards).toHaveLength(2);
      expect(cards.find((c) => c.dataset.artifactPath === 'src/new.py')).toHaveTextContent('print("now")');
      expect(cards.find((c) => c.dataset.artifactPath === 'src/app.py')).not.toHaveTextContent('+new');
      // Counts of changed lines belong to the diff mode, not to the files.
      expect(screen.queryByText('+1')).not.toBeInTheDocument();
      // A file carries the way back on its own line, before its name.
      const path = screen.getByText('app.py').closest('[data-artifact-path]').dataset.artifactPath;
      fireEvent.click(screen.getByText('app.py'));
      const back = screen.getByTestId('artifacts-back');
      expect(back.parentElement).toHaveTextContent(path);
      fireEvent.click(back);
      expect(screen.getByText('app.py').closest('[data-artifact-path]')).toBeInTheDocument();
    } finally {
      globalThis.ResizeObserver = RO;
      Element.prototype.getBoundingClientRect = rect;
    }
  });

  it('in a narrow panel shows the file text in Files mode and the diff in Diff mode', async () => {
    const RO = globalThis.ResizeObserver;
    const rect = Element.prototype.getBoundingClientRect;
    globalThis.ResizeObserver = class { observe() {} disconnect() {} };
    Element.prototype.getBoundingClientRect = function box() { return { width: 400, height: 600 }; };
    const card = () => screen.getAllByTestId('artifact-card').find((c) => c.dataset.artifactPath === 'src/app.py');
    try {
      const { rerender } = draw({ artifacts: ARTIFACTS, views: [], workspace: 'ws', mode: 'content' });
      await waitFor(() => expect(card()).toHaveTextContent('print("now")'));
      expect(card()).not.toHaveTextContent('+1');
      rerender(<I18nProvider><MemoryRouter><ArtifactsPanel artifacts={ARTIFACTS} views={[]} workspace="ws" mode="diff" /></MemoryRouter></I18nProvider>);
      expect(card()).toHaveTextContent('+new');
      expect(card()).toHaveTextContent('-old');
      expect(card()).toHaveTextContent('+1');
    } finally {
      globalThis.ResizeObserver = RO;
      Element.prototype.getBoundingClientRect = rect;
    }
  });
});
