import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n';
import { conversationArtifacts } from '../../chat/artifactOps';

vi.mock('../../stream', () => ({
  useStream: () => ({ on: () => () => {}, acquireChannel: () => () => {}, clientId: 'c1' }),
}));
vi.mock('../../../api', async (importOriginal) => ({
  ...(await importOriginal()),
  getRunTrail: vi.fn(() => Promise.resolve({ data: null })),
  getWorkspaceFileContent: vi.fn(() => Promise.resolve({ data: { content: '# Report\n\nSome **bold** text.' } })),
  stopMessage: vi.fn(),
  startChatOverSSE: vi.fn(),
}));
vi.mock('../../../views/ViewCard', () => ({ default: ({ viewRef }) => <div>view {viewRef.view_id}</div> }));

import FlowChat from '../FlowChat';
import FlowArtifacts from '../FlowArtifacts';

beforeAll(() => {
  Element.prototype.scrollIntoView = Element.prototype.scrollIntoView || (() => {});
  try { localStorage.clear(); } catch { /* no storage */ }
});

const resumed = [
  { id: 'u1', role: 'user', content: 'write it' },
  {
    id: 'a1', role: 'agent', agent_label: 'Writer', content: 'Done.',
    timeline: [
      { type: 'tool', tool: 'write_file', input: 'r.md', output: 'ok', status: 'ok' },
      { type: 'artifact', op: 'add', path: 'out/r.md', additions: 2, deletions: 0, diff: '+one\n+two', url: '/artifacts?file=f1' },
      { type: 'text', text: 'Done.' },
    ],
    entities: [{ kind: 'view', id: 'v1', title: 'Chart' }],
  },
];

const show = (onArtifacts) => render(
  <MemoryRouter><I18nProvider>
    <FlowChat flow={{ name: 'F' }} flowId="f1" workspace="ws" resumeConversationId="c" resumeMessages={resumed} resumeKey="k"
      onArtifacts={onArtifacts} />
  </I18nProvider></MemoryRouter>,
);

describe('Flow chat: the files and views for the page\'s Artifacts tab', () => {
  it('collects every node\'s files, the latest change of each, a later edit of an added file staying an add', () => {
    const out = conversationArtifacts([
      { role: 'agent', timeline: [{ type: 'artifact', op: 'add', path: 'a.md', diff: '+1', url: '/u' }] },
      { role: 'agent', timeline: [{ type: 'artifact', op: 'modify', path: 'a.md', diff: '+2' }] },
      { role: 'agent', files: [{ op: 'add', path: 'b.md' }] },
      { role: 'user', files: [{ op: 'add', path: 'ignored.md' }] },
    ]);
    expect(out).toEqual({
      'a.md': { op: 'add', path: 'a.md', diff: '+2', url: '/u' },
      'b.md': { op: 'add', path: 'b.md' },
    });
  });

  it('tells the page the conversation\'s files and views', async () => {
    const onArtifacts = vi.fn();
    show(onArtifacts);
    await waitFor(() => expect(onArtifacts).toHaveBeenLastCalledWith({
      artifacts: { 'out/r.md': expect.objectContaining({ op: 'add', diff: '+one\n+two', url: '/artifacts?file=f1' }) },
      views: [{ view_id: 'v1', title: 'Chart' }],
    }));
  });
});

describe('FlowArtifacts: the Artifacts tab', () => {
  it('lists the files and views, and shows a file\'s diff', () => {
    render(
      <MemoryRouter><I18nProvider>
        <FlowArtifacts
          artifacts={{ 'out/r.md': { op: 'add', path: 'out/r.md', additions: 2, diff: '+one\n+two' } }}
          views={[{ view_id: 'v1', title: 'Chart' }]}
          workspace="ws"
        />
      </I18nProvider></MemoryRouter>,
    );
    const panel = screen.getByTestId('flow-artifacts');
    expect(panel).toHaveTextContent('Chart');
    expect(panel).toHaveTextContent('r.md');
    // No header line of its own: the switch is on the picked file's line.
    expect(panel).not.toHaveTextContent('Artifacts');
    expect(screen.queryByRole('tab', { name: /diff/i })).toBeNull();
    fireEvent.click(panel.querySelector('[data-artifact-path="out/r.md"]'));
    fireEvent.click(screen.getByRole('tab', { name: /diff/i }));
    expect(screen.getByTestId('artifact-detail')).toHaveTextContent('+two');
  });
});

describe('FlowArtifacts: a Markdown file in Files', () => {
  it('reads as the document, in the panel, not as a code block', async () => {
    render(
      <MemoryRouter><I18nProvider>
        <FlowArtifacts artifacts={{ 'r.md': { op: 'add', path: 'r.md', diff: '+x' } }} workspace="ws" />
      </I18nProvider></MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('tab', { name: /^file$/i }));
    const doc = await screen.findByTestId('artifact-markdown');
    expect(doc.querySelector('h1')).toHaveTextContent('Report');
    expect(doc.querySelector('strong')).toHaveTextContent('bold');
    expect(document.querySelector('.hl-code-block')).toBeNull();
  });
});

describe('FlowArtifacts: the file\'s own page', () => {
  it('has Open on the picked file\'s line, to its page under Artifacts', () => {
    const onPick = vi.fn();
    render(
      <MemoryRouter><I18nProvider>
        <FlowArtifacts
          artifacts={{ 'out/r.md': { op: 'add', path: 'out/r.md', diff: '+x', url: '/artifacts?file=f1' } }}
          workspace="ws" picked="file:out/r.md" onPick={onPick}
        />
      </I18nProvider></MemoryRouter>,
    );
    expect(screen.getByTestId('artifact-open')).toHaveAttribute('href', '/artifacts?file=f1');
  });
});
