import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FeedItem } from '../flow/ChatFeed';
import { I18nProvider } from '../../i18n';

// What an agent chat shows while it works. The feed is the only window onto a
// turn that takes a minute, so each kind of step has to say something a reader
// can act on — which tool, on what, and what it changed.

const show = (item) => render(<I18nProvider><FeedItem e={item} /></I18nProvider>);

describe('FeedItem — tool steps', () => {
  it('summarises what the call is about, not just the tool name', () => {
    show({ k: 'tool', tool: 'modify_world_tool', status: 'running',
           input: '{"world_id": "wld_1", "add_locations": [{"name": "cellar"}]}' });
    expect(screen.getByText('modify_world_tool')).toBeInTheDocument();
    // The id is on screen already; which sections the edit touches is not.
    expect(screen.getByText(/add_locations/)).toBeInTheDocument();
    expect(screen.queryByText(/wld_1/)).not.toBeInTheDocument();
  });

  it('renders a tool with no arguments as the bare name', () => {
    show({ k: 'tool', tool: 'validate_world_tool', status: 'done' });
    expect(screen.getByText('validate_world_tool')).toBeInTheDocument();
  });

  it('shows why a failed tool failed', () => {
    show({ k: 'tool', tool: 'modify_world_tool', status: 'error', error: 'World not found' });
    expect(screen.getByText(/World not found/)).toBeInTheDocument();
  });
});

describe('FeedItem — thoughts', () => {
  // Reasoning is the longest thing in the feed and the least often read: a turn
  // with three thoughts in it pushed what the agent actually did off the top of
  // the panel, so it folds the way the main chat's Thought card does.
  const thought = 'First I check what the world already has, then I add the cellar.';

  it('folds a thought behind a label and its opening words', () => {
    show({ k: 'thinking', text: thought });
    expect(screen.getByText('Thought')).toBeInTheDocument();
    // The opening words are the handle you recognise it by, so they stay in
    // the document — it is the layout that clips them, not the render.
    expect(screen.getByText(thought)).toBeInTheDocument();
  });

  it('flattens the preview, and keeps the shape of the thought when opened', () => {
    const { container } = show({ k: 'thinking', text: `${thought}\n\nSecond paragraph.` });
    // Folded: one line, whatever the thought's own paragraphing was.
    expect(container.textContent).not.toContain('\n');
    fireEvent.click(screen.getByRole('button'));
    expect(container.textContent).toContain(`${thought}\n\nSecond paragraph.`);
    fireEvent.click(screen.getByRole('button'));
    expect(container.textContent).not.toContain('\n');
  });

  it('renders nothing for a thought with no words in it', () => {
    const { container } = show({ k: 'thinking', text: '   ' });
    expect(container).toBeEmptyDOMElement();
  });
});

describe('FeedItem — what the agent changed', () => {
  it('names the action, the kind and the thing', () => {
    show({ k: 'entity', action: 'added', kind: 'locations', label: 'cellar' });
    expect(screen.getByText('added')).toBeInTheDocument();
    expect(screen.getByText('location')).toBeInTheDocument();
    expect(screen.getByText('cellar')).toBeInTheDocument();
  });

  it('falls back to the raw kind for a section it has no word for', () => {
    show({ k: 'entity', action: 'set', kind: 'gravity', label: '0.5' });
    expect(screen.getByText('gravity')).toBeInTheDocument();
    expect(screen.getByText('0.5')).toBeInTheDocument();
  });

  it('sums up a change list too long to show in full', () => {
    show({ k: 'entity', action: 'more', kind: '', label: '8' });
    expect(screen.getByText(/8 more changes/)).toBeInTheDocument();
  });
});

describe('FeedItem — the conversation', () => {
  it('shows a reply that is still being written', () => {
    const { container } = show({ k: 'assistant', text: 'Adding the', live: true });
    expect(screen.getByText(/Adding the/)).toBeInTheDocument();
    expect(container.querySelector('.animate-pulse')).toBeTruthy();
  });

  it('renders nothing for a kind it does not know', () => {
    const { container } = show({ k: 'telemetry', text: 'ignored' });
    expect(container).toBeEmptyDOMElement();
  });
});
