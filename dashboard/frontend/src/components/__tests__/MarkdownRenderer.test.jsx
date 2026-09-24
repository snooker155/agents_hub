import { describe, expect, it } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import MarkdownRenderer from '../MarkdownRenderer';

const show = (content) => render(
  <I18nProvider>
    <MarkdownRenderer content={content} />
  </I18nProvider>,
);

describe('MarkdownRenderer: fenced code blocks', () => {
  it('highlights a python fence with token classes once the lazy loader resolves', async () => {
    const { container } = show('```python\ndef foo():\n    return "hi"\n```');
    await waitFor(() => {
      expect(container.querySelector('.tok-keyword')).toBeInTheDocument();
    });
    expect(container.querySelector('.tok-string')).toBeInTheDocument();
    // The plain body text is still fully present, just split across spans.
    expect(container.textContent).toContain('def foo():');
    expect(container.textContent).toContain('return "hi"');
  });

  it('renders an unrecognised language fence as plain text', async () => {
    const { container } = show('```notalanguage\nsome body text\n```');
    // Give any (non-existent) async highlighting a tick to settle.
    await waitFor(() => {
      expect(screen.getByText(/some body text/)).toBeInTheDocument();
    });
    expect(container.querySelector('[class^="tok-"]')).toBeNull();
  });

  it('shows a Copy button on every fenced block', async () => {
    show('```python\nprint(1)\n```');
    expect(await screen.findByTitle('Copy')).toBeInTheDocument();
  });
});
