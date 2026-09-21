/**
 * What makes a run somebody else's, and what has to be said about it.
 *
 * A reported run is deliberately indistinguishable from a local one in the
 * records: same row, same session, same costs. That is the point of observe
 * mode, and it is also why a list that mixes them needs one word of context.
 * Nothing here started an external run, so it has no log file, no live stream
 * and no stop button, and unmarked those absences read as a broken run rather
 * than as a run somebody else owns.
 *
 * The rule lives here, apart from the badges that draw it, because four pages
 * show runs and a rule written four times is a rule that drifts.
 */

/** True for a run that reported in rather than one this hub executed. */
export function isExternalRun(run) {
  if (!run) return false;
  return run.origin === 'ingest' || !!run.connection_id;
}

/** The OpenTelemetry import block, when the run arrived as spans rather than
 *  as frames from a tracer. Null for everything else. */
export function otelImport(run) {
  const otel = run?.metadata?.otel;
  return otel && otel.imported ? otel : null;
}

/** True when a trace was recorded without its root, so the record is missing
 *  the input, the answer and the outcome. */
export function isPartialImport(run) {
  const otel = otelImport(run);
  return !!otel && otel.root_reported === false;
}

/** Everything the mark has to explain, one sentence per fact. */
export function explainRunOrigin(run, t) {
  const parts = [t('components.runOrigin.externalHint', { connection: run?.connection_id || '' })];
  if (otelImport(run)) parts.push(t('components.runOrigin.importedHint'));
  if (isPartialImport(run)) parts.push(t('components.runOrigin.partialHint'));
  return parts.join(' ');
}
