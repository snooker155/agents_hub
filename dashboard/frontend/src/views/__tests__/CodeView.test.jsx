import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import CodeView from '../renderers/CodeView';

const view = {
  view_id: 'v1',
  kind: 'code',
  title: 'Snippet',
  spec: {
    language: 'python',
    filename: 'hello.py',
    body: 'print("hello")',
    dependencies: ['requests'],
    version: 3,
    description: 'A tiny script.',
  },
};

const show = (v) => render(
  <I18nProvider>
    <CodeView view={v} />
  </I18nProvider>,
);

describe('CodeView', () => {
  it('renders one header line with language, version, Download and Copy over the body', () => {
    show(view);
    // The card's header names the view; the block does not repeat the filename.
    expect(screen.queryByText('hello.py')).not.toBeInTheDocument();
    const header = document.querySelector('.hl-code-block__header');
    expect(header).toHaveTextContent('python');
    expect(header.querySelector('[data-testid="code-view-version"]')).toHaveTextContent('v3');
    const actions = header.querySelectorAll('.hl-code-block__action');
    expect([...actions].map((a) => a.textContent)).toEqual(['Download', 'Copy']);
    expect(screen.getByText(/print\("hello"\)/)).toBeInTheDocument();
    expect(screen.getByText('A tiny script.')).toBeInTheDocument();
    expect(screen.getByText('requests')).toBeInTheDocument();
  });

  it('shows no version chip when the spec has none', () => {
    show({ ...view, spec: { ...view.spec, version: undefined } });
    expect(document.querySelector('[data-testid="code-view-version"]')).toBeNull();
  });

  it('numbers the lines, a trailing newline not counted as one', () => {
    render(<I18nProvider><CodeView view={{ kind: 'code', spec: { language: 'python', body: 'a = 1\nb = 2\n' } }} /></I18nProvider>);
    expect(screen.getByTestId('code-line-numbers').textContent).toBe('1\n2');
  });
});
