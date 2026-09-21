import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { I18nProvider } from '../../i18n';
import { ExternalRunBadge, ImportedMark } from '../RunOriginBadges';
import { isExternalRun, isPartialImport, otelImport } from '../runOrigin';

// A reported run is deliberately identical to a local one in the records. The
// mark is the one place that difference is allowed to show, because an
// external run has no log, no live stream and no stop button, and without a
// word for that the absences read as a broken run.

const local = { run_id: 'r1', status: 'completed', agent_id: 'writer' };
const reported = { run_id: 'r2', status: 'completed', origin: 'ingest', connection_id: 'billing-graph' };
const imported = {
  ...reported, run_id: 'r3',
  metadata: { otel: { imported: true, root_reported: true, trace_id: 'abc' } },
};
const partial = {
  ...reported, run_id: 'r4',
  metadata: { otel: { imported: true, root_reported: false } },
};

const show = (ui) => render(<I18nProvider>{ui}</I18nProvider>);

describe('what counts as external', () => {
  it('is the run that reported in, however it was recognised', () => {
    expect(isExternalRun(local)).toBe(false);
    expect(isExternalRun(reported)).toBe(true);
    // Either marker alone is enough: a record written by an older build may
    // carry one and not the other.
    expect(isExternalRun({ origin: 'ingest' })).toBe(true);
    expect(isExternalRun({ connection_id: 'c' })).toBe(true);
    expect(isExternalRun(undefined)).toBe(false);
  });

  it('separates an import from a run that was watched as it happened', () => {
    expect(otelImport(reported)).toBeNull();
    expect(otelImport(imported)).toBeTruthy();
    expect(isPartialImport(imported)).toBe(false);
    expect(isPartialImport(partial)).toBe(true);
  });
});

describe('the badge in a general list', () => {
  it('says nothing about a run this hub executed', () => {
    const { container } = show(<ExternalRunBadge run={local} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('names the connection that reported it', () => {
    show(<ExternalRunBadge run={reported} />);
    const badge = screen.getByText('External');
    expect(badge.title).toContain('billing-graph');
    expect(badge.title).toMatch(/did not run it/i);
  });

  it('adds the import to the explanation without adding a second badge', () => {
    show(<ExternalRunBadge run={imported} />);
    const badge = screen.getByText('External');
    expect(badge.title).toMatch(/did not run it/i);
    expect(badge.title).toMatch(/no live view/i);
  });

  it('warns when the record itself is incomplete', () => {
    show(<ExternalRunBadge run={partial} />);
    expect(screen.getByText('External').title).toMatch(/root span never arrived/i);
  });
});

describe('the mark on a connection page', () => {
  it('stays quiet for a run reported live, where saying "external" is noise', () => {
    const { container } = show(<ImportedMark run={reported} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('marks an imported run, and flags one that lost its root', () => {
    show(<ImportedMark run={imported} />);
    expect(screen.getByText('imported').title).toMatch(/no live view/i);

    show(<ImportedMark run={partial} />);
    expect(screen.getByText('partial').title).toMatch(/root span never arrived/i);
  });
});
