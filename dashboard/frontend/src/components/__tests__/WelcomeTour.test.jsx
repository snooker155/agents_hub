import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import {
  TOUR_DONE_KEY, TOUR_STOPS, buildSteps, createTourRunner, waitForElement, isTourDone,
} from '../docs/tourRunner';

vi.mock('driver.js', () => {
  const highlight = vi.fn();
  const destroy = vi.fn();
  return {
    driver: vi.fn(() => ({ highlight, destroy, isActive: () => true })),
    __spies: { highlight, destroy },
  };
});
vi.mock('driver.js/dist/driver.css', () => ({}));

const t = (key, vars) => (vars ? `${key}:${JSON.stringify(vars)}` : key);

function mountEl(selector) {
  const el = document.createElement('div');
  if (selector.startsWith('#')) el.id = selector.slice(1);
  document.body.appendChild(el);
  return el;
}

describe('welcome tour steps', () => {
  it('visits the pages in the documented order', () => {
    expect(TOUR_STOPS.map((s) => s.id)).toEqual([
      'chat', 'agents', 'tasks', 'flows', 'teams', 'playground', 'views', 'health', 'docs',
    ]);
    const steps = buildSteps(t, { playground: true });
    expect(steps.map((s) => s.path)).toEqual([
      '/chat', '/agents', '/tasks', '/flows', '/teams', '/playground', '/views', '/health', '/docs',
    ]);
    expect(steps[0].title).toBe('tour.steps.chat.title');
  });

  it('leaves the playground out when it is disabled', () => {
    expect(buildSteps(t, { playground: false }).map((s) => s.id)).not.toContain('playground');
  });
});

describe('tour runner', () => {
  beforeEach(() => {
    localStorage.clear();
    document.body.innerHTML = '';
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it('skips a step whose element never appears, after the timeout', async () => {
    mountEl('#one');
    mountEl('#three');
    const steps = [
      { id: 'a', path: '/a', element: '#one' },
      { id: 'b', path: '/b', element: '#missing' },
      { id: 'c', path: '/c', element: '#three' },
    ];
    let path = '/a';
    const navigate = vi.fn((p) => { path = p; });
    const shown = [];
    const runner = createTourRunner({
      steps, navigate, currentPath: () => path,
      show: (step) => shown.push(step.id), hide: vi.fn(),
    });

    await runner.start();
    expect(shown).toEqual(['a']);
    expect(navigate).not.toHaveBeenCalled();

    const pending = runner.next();
    // Still waiting on #missing: nothing new yet.
    await vi.advanceTimersByTimeAsync(2999);
    expect(shown).toEqual(['a']);
    await vi.advanceTimersByTimeAsync(1);
    await pending;
    expect(shown).toEqual(['a', 'c']);
    expect(navigate.mock.calls.map((c) => c[0])).toEqual(['/b', '/c']);
  });

  it('picks up an element rendered late', async () => {
    const found = waitForElement('#late', { timeout: 3000 });
    await vi.advanceTimersByTimeAsync(500);
    mountEl('#late');
    await vi.advanceTimersByTimeAsync(0);
    expect((await found)?.id).toBe('late');
  });

  it('marks completion in localStorage after the last step', async () => {
    mountEl('#x');
    const onFinish = vi.fn();
    const runner = createTourRunner({
      steps: [{ id: 'x', path: '/x', element: '#x' }],
      navigate: vi.fn(), currentPath: () => '/x', show: vi.fn(), hide: vi.fn(), onFinish,
    });
    await runner.start();
    expect(isTourDone()).toBe(false);
    runner.next();
    expect(localStorage.getItem(TOUR_DONE_KEY)).toBe('1');
    expect(onFinish).toHaveBeenCalledWith(true);
  });

  it('marks completion when closed early too', async () => {
    mountEl('#x');
    const runner = createTourRunner({
      steps: [{ id: 'x', path: '/x', element: '#x' }, { id: 'y', path: '/y', element: '#y' }],
      navigate: vi.fn(), currentPath: () => '/x', show: vi.fn(), hide: vi.fn(),
    });
    await runner.start();
    runner.close();
    expect(isTourDone()).toBe(true);
    expect(runner.finished).toBe(true);
  });
});

describe('useWelcomeTour', () => {
  beforeEach(() => {
    localStorage.clear();
    document.body.innerHTML = '';
  });

  it('highlights the first step through driver.js', async () => {
    const { useWelcomeTour } = await import('../docs/WelcomeTour');
    const { I18nProvider } = await import('../../i18n');
    const { __spies } = await import('driver.js');
    function Probe() {
      const tour = useWelcomeTour();
      return <main><textarea /><button type="button" onClick={() => tour.start()}>go</button></main>;
    }
    window.history.replaceState(null, '', '/chat');
    const { getByText } = render(<I18nProvider><MemoryRouter initialEntries={['/chat']}><Probe /></MemoryRouter></I18nProvider>);
    await act(async () => { fireEvent.click(getByText('go')); });
    expect(__spies.highlight).toHaveBeenCalled();
    const arg = __spies.highlight.mock.calls[0][0];
    expect(arg.popover.title).toBe('Chat');
    expect(arg.element.tagName).toBe('TEXTAREA');
  });
});
