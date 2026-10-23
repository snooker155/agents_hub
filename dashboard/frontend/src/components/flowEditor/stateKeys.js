/**
 * A flow state key as a person reads it: the hub's own keys by name
 * (`flowEditor.stateKeys`), a key the flow declared written out as words
 * ("market_findings" reads "Market findings"). The key itself stays what a
 * node's inputs and outputs refer to.
 */
export function humanizeKey(key) {
  const words = String(key || '').replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').trim().toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : String(key || '');
}

export function stateKeyLabel(key, t) {
  return t(`flowEditor.stateKeys.${key}`, { defaultValue: humanizeKey(key) });
}

/**
 * The state a run of `flow` starts from, as the engine builds it
 * (flow/engine.py): `user_request` is always there, empty until something is
 * asked, and the keys the flow declares come on top (a declared
 * `user_request` included).
 */
export function initialFlowState(flow) {
  const declared = flow?.state && typeof flow.state === 'object' && !Array.isArray(flow.state) ? flow.state : {};
  return { user_request: '', ...declared };
}
