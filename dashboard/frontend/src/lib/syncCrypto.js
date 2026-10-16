/**
 * Opening synced records in the browser (hubsync/crypto.py is the other half;
 * the formats match byte for byte). The passphrase and every plaintext stay in
 * this tab's memory: nothing here touches storage or the network.
 *
 * The key is a non-extractable AES-GCM CryptoKey. Its raw bytes exist only
 * long enough to compute the key id, then are overwritten.
 *
 * Envelope version 2 binds a record to its space as well as its kind and id
 * (one key seals every space a person syncs, so without the space a server
 * could replay one space's record in another). It is the only version: sync
 * was never released with version 1, so no such data exists, and reading it
 * would reopen the replay hole, since a version 1 envelope names no space.
 */

const ENC = new TextEncoder();
const DEC = new TextDecoder();
const KID_LABEL = 'agents-hub/v1|key-id';
const VERIFIER_TEXT = 'agents-hub sync key check v1';
const VERIFIER_AAD = 'agents-hub/v1|verifier';

export class SyncCryptoError extends Error {}

const subtle = () => globalThis.crypto.subtle;

export const b64decode = (text) => {
  const bin = atob(text);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
  return out;
};

const hex = (buf) => Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, '0')).join('');

const VERSION = 2;

export const recordAad = (kind, recordId, space) => {
  if (space === undefined || space === null || space === '') {
    throw new SyncCryptoError('opening a record needs its space');
  }
  return `agents-hub/v${VERSION}|${space}|${kind}|${recordId}`;
};

/** Derive the AES key from a passphrase. Returns { key, keyId }. */
export async function deriveKey(passphrase, { salt, iterations }) {
  if (!passphrase) throw new SyncCryptoError('empty passphrase');
  const base = await subtle().importKey('raw', ENC.encode(passphrase), 'PBKDF2', false, ['deriveBits']);
  const bits = await subtle().deriveBits(
    { name: 'PBKDF2', hash: 'SHA-256', salt: b64decode(salt), iterations: Number(iterations) },
    base,
    256,
  );
  const raw = new Uint8Array(bits);
  const label = ENC.encode(KID_LABEL);
  const joined = new Uint8Array(label.length + raw.length);
  joined.set(label);
  joined.set(raw, label.length);
  const keyId = hex(await subtle().digest('SHA-256', joined)).slice(0, 16);
  const key = await subtle().importKey('raw', raw, { name: 'AES-GCM' }, false, ['decrypt']);
  raw.fill(0);
  joined.fill(0);
  return { key, keyId };
}

async function openEnvelope(key, envelope, aad) {
  if (!envelope || envelope.v !== VERSION || envelope.alg !== 'A256GCM') {
    throw new SyncCryptoError('unknown envelope format');
  }
  try {
    const plain = await subtle().decrypt(
      { name: 'AES-GCM', iv: b64decode(envelope.n), additionalData: ENC.encode(aad) },
      key,
      b64decode(envelope.c),
    );
    return new Uint8Array(plain);
  } catch {
    throw new SyncCryptoError('the key does not open this record');
  }
}

/** True when the key opens the verifier the remote stores beside the KDF parameters. */
export async function checkVerifier(key, verifier) {
  try {
    const envelope = typeof verifier === 'string' ? JSON.parse(verifier) : verifier;
    const plain = await openEnvelope(key, envelope, VERIFIER_AAD);
    return DEC.decode(plain) === VERIFIER_TEXT;
  } catch {
    return false;
  }
}

// Node's zlib stands in where jsdom lacks DecompressionStream (tests only).
let fallbackGunzip = null;
export const setGunzipFallback = (fn) => { fallbackGunzip = fn; };

export async function gunzip(bytes) {
  if (fallbackGunzip) return fallbackGunzip(bytes);
  if (typeof DecompressionStream === 'undefined') {
    throw new SyncCryptoError('gzip is not available in this browser');
  }
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/**
 * Turn a stored record into { kind, id, title, data }. `key` is needed only for
 * an encrypted record, and with a key a plain record is refused; a wrong key,
 * a swapped record id or a record from another space throws SyncCryptoError.
 * `space` is the space the page asked for and is required for an encrypted
 * record: the space the server echoes in the record is ignored, because a
 * server replaying a record from another space would echo that space too.
 */
export async function decodeRecord({ payload, encrypted, kind, record_id: recordId }, key, { space } = {}) {
  let packed;
  if (encrypted) {
    if (!key) throw new SyncCryptoError('the record is encrypted and no key is unlocked');
    let envelope;
    try { envelope = JSON.parse(payload); } catch { throw new SyncCryptoError('damaged envelope'); }
    packed = await openEnvelope(key, envelope, recordAad(kind, recordId, space));
  } else {
    // A key means the space is encrypted: a plain record there is one the
    // server made up, since a client never pushes one into such a space.
    if (key) throw new SyncCryptoError('a plain record in an encrypted space');
    packed = b64decode(payload);
  }
  return JSON.parse(DEC.decode(await gunzip(packed)));
}
