import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../../../i18n';
import { MessageFiles } from '../messageParts';
import { FileOpenContext } from '../fileOpenContext';

const show = (props) => render(
  <MemoryRouter><I18nProvider><MessageFiles {...props} /></I18nProvider></MemoryRouter>,
);

describe('MessageFiles: the files a turn changed, as links to the files', () => {
  it('links each file to its page, not to a diff', () => {
    show({
      files: [
        { op: 'add', path: 'report.md', additions: 12, deletions: 0, url: '/artifacts?file=file_1' },
        { op: 'modify', path: 'notes.md', additions: 1, deletions: 1 },
        { op: 'delete', path: 'old.md', additions: 0, deletions: 4 },
      ],
      // A change recorded before it carried its link finds it among the turn's entities.
      entities: [{ kind: 'file', id: 'ws/notes.md', title: 'notes.md', url: '/artifacts?file=file_2' }],
    });
    expect(screen.getByText('report.md').closest('a')).toHaveAttribute('href', '/artifacts?file=file_1');
    expect(screen.getByText('notes.md').closest('a')).toHaveAttribute('href', '/artifacts?file=file_2');
    // A deleted file has no page left.
    expect(screen.getByText('old.md').closest('a')).toBeNull();
    expect(screen.queryByText(/@@/)).toBeNull();
    // A small block per file, its name and nothing of the diff.
    expect(screen.queryByText(/\+12/)).toBeNull();
    expect(screen.queryByText(/files? changed/i)).toBeNull();
  });

  it('shows a file once, by its name, the full path on hover', () => {
    show({ files: [
      { op: 'add', path: 'out/report.md', url: '/artifacts?file=file_1' },
      { op: 'modify', path: 'out/report.md', url: '/artifacts?file=file_1' },
    ] });
    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveTextContent('report.md');
    expect(links[0].getAttribute('title')).toContain('out/report.md');
  });

  it('shows the file in the artifacts beside the chat where the page has them', () => {
    const openFile = vi.fn();
    render(
      <MemoryRouter><I18nProvider><FileOpenContext.Provider value={openFile}>
        <MessageFiles files={[
          { op: 'add', path: 'out/r.md', url: '/artifacts?file=f1' },
          { op: 'delete', path: 'old.md' },
        ]} />
      </FileOpenContext.Provider></I18nProvider></MemoryRouter>,
    );
    expect(screen.getByText('r.md').closest('a')).toBeNull();
    fireEvent.click(screen.getByText('r.md'));
    expect(openFile).toHaveBeenCalledWith('out/r.md');
    // A deleted file has nothing to show.
    expect(screen.getByText('old.md').closest('button')).toBeNull();
  });
});
