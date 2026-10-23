/*
 * A channel of its own per decomposition (routes/task_decompose.py), so the
 * stream shows this one and nothing else. crypto.randomUUID is missing outside
 * a secure context (plain http on a LAN address), hence the fallback.
 */
export const newDecomposeChannel = () => `decompose:${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
