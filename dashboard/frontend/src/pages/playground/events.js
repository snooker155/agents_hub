/**
 * The world's own lines for a run, up to the scrubber's tick.
 *
 * Its own module because both the feed and the tab that badges it have to count
 * the same thing: if the badge counted ticks and the feed rendered lines they
 * would drift the first time one tick wrote two events.
 *
 * Refused actions belong here as well as inline on the turn that caused them —
 * a refusal is a fact about the world, not only about the agent.
 */
export function eventLines(ticks, cursor) {
  return ticks.slice(0, cursor + 1).flatMap((tk) => [
    ...(tk.events || []).map((text, i) => (
      { key: `${tk.tick}-e${i}`, tick: tk.tick, text, kind: 'event' }
    )),
    ...(tk.resolutions || []).filter((r) => !r.ok).map((r, i) => ({
      key: `${tk.tick}-r${i}`, tick: tk.tick, kind: 'refused',
      text: `${r.agent}: ${r.action} — ${r.message}`,
    })),
  ]);
}

export default eventLines;
