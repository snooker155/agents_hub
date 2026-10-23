import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n';

vi.mock('../files/TaskFilesCard', () => ({ default: () => null }));

import { TaskFilesTab } from '../task/TaskFilesTab';
import { buildFileTree } from '../task/taskUtils';

const files = ['index.html', 'styles.css'];
const show = (props) => render(
  <MemoryRouter><I18nProvider>
    <TaskFilesTab
      task={{ id: 't' }} id="t" workspaceFiles={files} fileTree={buildFileTree(files)}
      expandedFolders={new Set()} setExpandedFolders={vi.fn()} toggleFolder={vi.fn()}
      loadFileContent={vi.fn()} mdViewMode="rendered" pdfViewMode="render"
      setMdViewMode={vi.fn()} setPdfViewMode={vi.fn()} fileContentError=""
      selectedFileIsPdf={false} selectedFileSize={10} {...props}
    />
  </I18nProvider></MemoryRouter>,
);

describe('TaskFilesTab switching files', () => {
  it('keeps the last file in view, dimmed, while the next one loads', () => {
    show({ selectedFilePath: 'styles.css', selectedFileContent: '<html>menu</html>', fileContentLoading: true });
    const preview = screen.getByTestId('file-preview');
    expect(preview).toHaveTextContent('<html>menu</html>');
    expect(preview.className).toMatch(/opacity-50/);
    expect(screen.queryByRole('status')).toBeNull();   // no loader swapped in
  });

  it('shows the loader only before any file is in', () => {
    show({ selectedFilePath: 'index.html', selectedFileContent: '', fileContentLoading: true });
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});
