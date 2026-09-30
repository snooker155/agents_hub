import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { I18nProvider } from '../../../i18n';
import ChatMarkdown from '../ChatMarkdown';
import { ChatCodeActionsContext, ChatMathActionsContext } from '../chatMarkdownContext';

const show = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

describe('ChatMarkdown', () => {
  it('renders headings, lists, emphasis, links and tables', () => {
    const { container } = show(
      <ChatMarkdown content={'## Plan\n\n1. **First** step\n2. *Second*\n\n- [docs](https://x.y)\n\n| a | b |\n|---|---|\n| 1 | 2 |'} />,
    );
    expect(screen.getByRole('heading', { level: 2, name: 'Plan' })).toBeInTheDocument();
    expect(container.querySelector('ol li strong')).toHaveTextContent('First');
    expect(container.querySelector('ol li em')).toHaveTextContent('Second');
    expect(screen.getByRole('link', { name: 'docs' })).toHaveAttribute('target', '_blank');
    expect(container.querySelector('table td')).toHaveTextContent('1');
  });

  it('shows a fenced block with its language and a copy button, and no line breaks around it', () => {
    const { container } = show(<ChatMarkdown content={'Try this:\n\n```python\nprint(1)\n```\n\nThen run it.'} />);
    const block = screen.getByTestId('code-block');
    expect(block).toHaveTextContent('python');
    expect(block.querySelector('pre code')).toHaveTextContent('print(1)');
    expect(screen.getByRole('button', { name: /Copy/ })).toBeInTheDocument();
    expect(container.querySelectorAll('br')).toHaveLength(0);
    expect(container.querySelectorAll('p')).toHaveLength(2);
  });

  it('highlights a known language', async () => {
    const { container } = show(<ChatMarkdown content={'```python\ndef f():\n    return 1\n```'} />);
    await waitFor(() => expect(container.querySelector('.tok-keyword')).not.toBeNull());
  });

  it('keeps a single newline as a line break', () => {
    const { container } = show(<ChatMarkdown content={'line one\nline two'} />);
    expect(container.querySelectorAll('br')).toHaveLength(1);
  });

  it('renders markup still being streamed in its final shape', () => {
    const { container } = show(<ChatMarkdown content={'This is **important'} streaming />);
    expect(container.querySelector('strong')).toHaveTextContent('important');
    const open = show(<ChatMarkdown content={'Code:\n\n```js\nconst a = 1;'} streaming />);
    expect(open.getAllByTestId('code-block').at(-1)).toHaveTextContent('const a = 1;');
  });

  it('shows raw HTML as text', () => {
    const { container } = show(<ChatMarkdown content={'<img src=x onerror="alert(1)"> hi'} />);
    expect(container.querySelector('img')).toBeNull();
  });

  it('offers "Open in Code panel" only where there is a panel to open', async () => {
    const content = '```bash\necho hi\n```';
    const first = show(<ChatMarkdown content={content} />);
    expect(first.queryByTestId('code-open-in-panel')).toBeNull();
    first.unmount();

    const open = vi.fn(() => Promise.resolve());
    show(
      <ChatCodeActionsContext.Provider value={{ open }}>
        <ChatMarkdown content={content} />
      </ChatCodeActionsContext.Provider>,
    );
    fireEvent.click(screen.getByTestId('code-open-in-panel'));
    expect(open).toHaveBeenCalledWith('echo hi', 'bash');
    await waitFor(() => expect(screen.getByTestId('code-open-in-panel')).not.toBeDisabled());
  });

  it('links known citation markers outside code only', () => {
    const onCite = vi.fn();
    show(
      <ChatMarkdown
        content={'Thirty days [1], see [9].\n\n```\nx = arr[1]\n```'}
        citations={[{ n: 1 }]}
        onCite={onCite}
      />,
    );
    const markers = screen.getAllByTestId('citation-marker');
    expect(markers).toHaveLength(1);
    fireEvent.click(markers[0]);
    expect(onCite).toHaveBeenCalledWith(1);
    expect(screen.getByText(/\[9\]/)).toBeInTheDocument();
  });
  it('renders formulas with KaTeX, inline and display, and leaves prices as text', async () => {
    const { container } = show(
      <ChatMarkdown content={'Energy \\(E = mc^2\\) costs $5 to $10.\n\n$$\\int_0^1 x\\,dx = \\frac{1}{2}$$'} />,
    );
    await waitFor(() => expect(container.querySelector('.katex-display')).not.toBeNull());
    expect(container.querySelectorAll('.katex')).toHaveLength(2);
    expect(container.querySelector('.katex-display annotation')).toHaveTextContent('\\int_0^1 x\\,dx = \\frac{1}{2}');
    expect(container.textContent).toContain('costs $5 to $10.');
  });

  it('holds back a formula still arriving', () => {
    const { container } = show(<ChatMarkdown streaming content={'The sum is\n$$\n\\sum_{i=1}^{n'} />);
    expect(container.textContent).toBe('The sum is');
  });
  it('saves a display formula to memory under the title given, as its TeX', async () => {
    const save = vi.fn(() => Promise.resolve());
    show(
      <ChatMathActionsContext.Provider value={{ save }}>
        <ChatMarkdown content={'Euler:\n\n$$e^{i\\pi} + 1 = 0$$\n\nand inline $x^2$.'} />
      </ChatMathActionsContext.Provider>,
    );
    expect(screen.getAllByTestId('formula-save')).toHaveLength(1);
    fireEvent.click(screen.getByTestId('formula-save'));
    fireEvent.change(screen.getByTestId('formula-title'), { target: { value: 'Euler identity' } });
    fireEvent.submit(screen.getByTestId('formula-title').closest('form'));
    await waitFor(() => expect(screen.getByTestId('formula-saved')).toBeInTheDocument());
    expect(save).toHaveBeenCalledWith('e^{i\\pi} + 1 = 0', 'Euler identity');
  });

  it('offers no save without a chat to save from', () => {
    show(<ChatMarkdown content={'$$x$$'} />);
    expect(screen.queryByTestId('formula-save')).toBeNull();
  });
});
