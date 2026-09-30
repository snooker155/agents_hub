import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nProvider } from '../../i18n';

vi.mock('../../api', () => ({
  exportViewPptx: vi.fn(() => Promise.resolve({ data: new Blob(['pptx']) })),
  viewAssetUrl: (id, name) => `/api/views/${id}/assets/${name}`,
}));
vi.mock('../../api/files', () => ({ saveBlobAs: vi.fn() }));

import { exportViewPptx } from '../../api';
import { saveBlobAs } from '../../api/files';
import SlidesView from '../renderers/SlidesView';
import { inlineRuns, parseMarkdown } from '../slides/markdown';
import THEMES from '../slideThemes.json';

const deck = {
  view_id: 'vw_1',
  kind: 'slides',
  title: 'What is new',
  spec: {
    theme: 'ocean',
    footer: 'Team · 2026',
    slides: {
      b: { order: 1, layout: 'stats', title: 'In numbers', items: [{ value: '8', title: 'features', text: 'deploy, releases' }] },
      a: { order: 0, layout: 'title', title: 'What is new', subtitle: 'September', icon: '🚀' },
      c: { order: 2, layout: 'content', title: 'Deploy', body: '- **compose** files\n- a link per app', notes: 'Say it slowly.' },
      d: { order: 3, layout: 'cards', title: 'Highlights', items: [{ icon: '📦', title: 'Releases', text: 'one version' }, { icon: '💬', title: 'Widget', text: 'chat anywhere' }] },
      e: { order: 4, layout: 'timeline', title: 'Stages', items: [{ value: '22 Sep', title: 'Audit' }, { value: '29 Sep', title: 'Deploy' }] },
      f: { order: 5, layout: 'two_column', title: 'Before and after', columns: ['### Before\n- manual', '### After\n- one click'] },
      g: { order: 6, layout: 'image_left', title: 'Preview', image: 'asset://shot.png', caption: 'Inside the hub', body: 'Live' },
      h: { order: 7, layout: 'quote', body: 'All in one place.', subtitle: 'the team' },
      i: { order: 8, layout: 'section', title: 'Part two' },
      j: { order: 9, layout: 'image_full', title: 'Thanks', image: 'https://example.com/x.png' },
    },
  },
};

const show = (v = deck) => render(<I18nProvider><SlidesView view={v} /></I18nProvider>);
const stage = () => screen.getByTestId('slide-stage');
const root = () => screen.getByTestId('slides-view');

describe('SlidesView', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the slides in order, starting with the cover', () => {
    show();
    expect(screen.getByTestId('slide-counter').textContent).toBe('1 / 10');
    expect(stage().textContent).toContain('What is new');
    expect(stage().textContent).toContain('September');
    expect(stage().textContent).toContain('🚀');
  });

  it('draws every layout without losing its content', () => {
    show();
    const seen = [];
    for (let i = 0; i < 10; i += 1) {
      seen.push(stage().textContent);
      fireEvent.keyDown(root(), { key: 'ArrowRight' });
    }
    const text = seen.join('\n');
    for (const piece of ['In numbers', '8', 'features', 'compose', 'a link per app', 'Releases', 'chat anywhere',
      '22 Sep', 'Deploy', 'Before', 'one click', 'Inside the hub', 'All in one place.', '— the team', 'Part two', '01', 'Thanks']) {
      expect(text).toContain(piece);
    }
    expect(text).toContain('Team · 2026');
  });

  it('resolves an asset image against the view and keeps a URL as it is', () => {
    show();
    fireEvent.keyDown(root(), { key: 'End' });
    expect(stage().querySelector('img').getAttribute('src')).toBe('https://example.com/x.png');
    fireEvent.keyDown(root(), { key: 'ArrowLeft' });
    fireEvent.keyDown(root(), { key: 'ArrowLeft' });
    fireEvent.keyDown(root(), { key: 'ArrowLeft' });
    expect(stage().querySelector('img').getAttribute('src')).toBe('/api/views/vw_1/assets/shot.png');
  });

  it('paints with the deck theme', () => {
    show();
    // stage > scaled box > scaled content > slide root
    const slideRoot = () => stage().firstElementChild.firstElementChild.firstElementChild;
    const toRgb = (hex) => `rgb(${[1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
    expect(slideRoot().style.color).toBe(toRgb(THEMES.ocean.heroText));
    fireEvent.keyDown(root(), { key: 'ArrowRight' });
    fireEvent.keyDown(root(), { key: 'ArrowRight' });
    expect(slideRoot().style.background).toBe(toRgb(THEMES.ocean.bg));
    expect(slideRoot().style.color).toBe(toRgb(THEMES.ocean.text));
  });

  it('ignores keys typed into a field inside the viewer', () => {
    show();
    const input = document.createElement('input');
    root().appendChild(input);
    fireEvent.keyDown(input, { key: 'ArrowRight' });
    expect(screen.getByTestId('slide-counter').textContent).toBe('1 / 10');
  });

  it('shows thumbnails and jumps to the one clicked', () => {
    show();
    fireEvent.click(screen.getByTitle('Slide overview'));
    const thumbs = screen.getByTestId('slide-thumbs').querySelectorAll('button');
    expect(thumbs).toHaveLength(10);
    fireEvent.click(thumbs[2]);
    expect(screen.getByTestId('slide-counter').textContent).toBe('3 / 10');
  });

  it('shows speaker notes for the current slide', () => {
    show();
    fireEvent.keyDown(root(), { key: 'Home' });
    fireEvent.keyDown(root(), { key: 'n' });
    expect(screen.getByTestId('slide-notes').textContent).toContain('No notes');
    fireEvent.keyDown(root(), { key: 'ArrowRight' });
    fireEvent.keyDown(root(), { key: 'ArrowRight' });
    expect(screen.getByTestId('slide-notes').textContent).toContain('Say it slowly.');
  });

  it('downloads the deck as .pptx through the API', async () => {
    show();
    fireEvent.click(screen.getByTitle('Download as PowerPoint (.pptx)'));
    await waitFor(() => expect(saveBlobAs).toHaveBeenCalled());
    expect(exportViewPptx).toHaveBeenCalledWith('vw_1');
    expect(saveBlobAs.mock.calls[0][1]).toBe('What is new.pptx');
  });

  it('says when the .pptx could not be built', async () => {
    exportViewPptx.mockImplementationOnce(() => Promise.reject(new Error('500')));
    show();
    fireEvent.click(screen.getByTitle('Download as PowerPoint (.pptx)'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not build');
  });

  it('reads a deck stored as a list', () => {
    show({ ...deck, spec: { slides: [{ title: 'One', body: 'x' }, { title: 'Two', body: 'y' }] } });
    expect(screen.getByTestId('slide-counter').textContent).toBe('1 / 2');
    expect(stage().textContent).toContain('One');
  });

  it('asks the agent for slides when the deck is empty', () => {
    show({ ...deck, spec: { slides: {} } });
    expect(screen.getByText(/Empty deck/)).toBeInTheDocument();
  });
});

describe('slide markdown', () => {
  it('parses the blocks the .pptx export parses', () => {
    const blocks = parseMarkdown('# Title\n\n- one\n  - nested\n1. first\n2. second\n\n> quoted\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```\n---\ntext\nmore');
    expect(blocks.map((b) => b.kind)).toEqual(['heading', 'bullet', 'bullet', 'bullet', 'bullet', 'quote', 'table', 'code', 'hr', 'para']);
    expect(blocks[2]).toMatchObject({ level: 1, ordered: false });
    expect(blocks[4]).toMatchObject({ ordered: true, number: 2 });
    expect(blocks[6].rows).toEqual([['a', 'b'], ['1', '2']]);
    expect(blocks[9].text).toBe('text more');
  });

  it('splits inline formatting', () => {
    expect(inlineRuns('a **b** *c* `d` [e](https://x.y)')).toEqual([
      ['a ', {}], ['b', { bold: true }], [' ', {}], ['c', { italic: true }], [' ', {}],
      ['d', { code: true }], [' ', {}], ['e', { link: 'https://x.y' }],
    ]);
  });
});
