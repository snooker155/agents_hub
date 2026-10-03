import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import FileViewer from '../FileViewer';
import { I18nProvider } from '../../../i18n';

const show = (props) => render(<I18nProvider><FileViewer {...props} /></I18nProvider>);

describe('FileViewer', () => {
  it('renders Markdown, and shows its source on request', () => {
    const { container, unmount } = show({ name: 'README.md', text: '# Title\n\nBody' });
    expect(container.querySelector('h1')?.textContent).toBe('Title');
    unmount();
    show({ name: 'README.md', text: '# Title\n\nBody', source: true });
    expect(screen.getByText('markdown')).toBeTruthy();
  });

  it('highlights code with its language', () => {
    show({ name: 'src/app.py', text: "print('hi')\n" });
    expect(screen.getByText('python')).toBeTruthy();
  });

  it('shows an HTML page in a sandboxed frame', () => {
    const { container } = show({ name: 'index.html', url: 'blob:x', text: '<p>x</p>' });
    const frame = container.querySelector('iframe');
    expect(frame.getAttribute('sandbox')).toBe('');
  });

  it('shows a PDF in an unsandboxed frame, or its extracted text', () => {
    const { container, unmount } = show({ name: 'spec.pdf', url: 'blob:x', text: 'page one', kind: 'pdf' });
    expect(container.querySelector('iframe').hasAttribute('sandbox')).toBe(false);
    unmount();
    show({ name: 'spec.pdf', url: 'blob:x', text: 'page one', kind: 'pdf', source: true });
    expect(screen.getByText('Text extracted from the PDF')).toBeTruthy();
    expect(screen.getByText('page one')).toBeTruthy();
  });

  it('shows an image, and says when there is nothing to show', () => {
    const { container, unmount } = show({ name: 'logo.png', mimeType: 'image/png', url: 'blob:x', kind: 'binary' });
    expect(container.querySelector('img')).toBeTruthy();
    unmount();
    show({ name: 'archive.zip', kind: 'binary' });
    expect(screen.getByText(/No preview for this type of file/)).toBeTruthy();
  });

  it('says a cut preview is cut', () => {
    show({ name: 'notes.txt', text: 'abc', truncated: true });
    expect(screen.getByText('The preview shows the beginning of the file.')).toBeTruthy();
  });
});
