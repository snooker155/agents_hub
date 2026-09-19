// Client mirror of the backend view op algebra (views/ops.py). The Studio holds
// the live document in React state and applies each streamed op locally so the
// canvas updates instantly, without refetching the whole view per op. Semantics
// must match the server: dotted-path set/remove/clear over keyed-map collections.

function split(path) {
  return String(path || '').split('.').filter((p) => p !== '');
}

// Apply one op to a shallow-cloned document, returning the new document.
export function applyOp(doc, op) {
  const next = clone(doc);
  const kind = op?.op;
  if (kind === 'clear' && !String(op?.path || '').trim()) {
    next.spec = {};
    return next;
  }
  const parts = split(op?.path);
  if (!parts.length) return next;

  let parent = next;
  for (const seg of parts.slice(0, -1)) {
    if (typeof parent[seg] !== 'object' || parent[seg] === null || Array.isArray(parent[seg])) {
      if (kind === 'remove' || kind === 'clear') return next; // nothing along a missing path
      parent[seg] = {};
    } else {
      parent[seg] = Array.isArray(parent[seg]) ? [...parent[seg]] : { ...parent[seg] };
    }
    parent = parent[seg];
  }
  const leaf = parts[parts.length - 1];
  if (kind === 'add' || kind === 'update') parent[leaf] = op.value;
  else if (kind === 'remove') delete parent[leaf];
  else if (kind === 'clear') parent[leaf] = {};
  return next;
}

export function foldOps(base, ops) {
  return (ops || []).reduce((doc, op) => applyOp(doc, op), clone(base || {}));
}

function clone(obj) {
  if (Array.isArray(obj)) return [...obj];
  if (obj && typeof obj === 'object') return { ...obj };
  return obj;
}

// A short human label for an op, for the Studio's build-status strip.
export function opLabel(op) {
  const tail = String(op?.path || '').split('.').slice(-2).join('.');
  const verb = { add: '+', update: '~', remove: '−', clear: '⟲' }[op?.op] || '?';
  return `${verb} ${tail}`;
}
