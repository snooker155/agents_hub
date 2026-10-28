import { describe, it, expect, beforeAll } from 'vitest';
import { gunzipSync } from 'node:zlib';
import { webcrypto } from 'node:crypto';
import { Buffer } from 'node:buffer';

import vector from './fixtures/syncVector.json';
import { deriveKey, checkVerifier, decodeRecord, setGunzipFallback, SyncCryptoError } from '../syncCrypto';

// The vector comes from hubsync/crypto.py, so these hold the two halves together.
beforeAll(() => {
  if (!globalThis.crypto?.subtle) globalThis.crypto = webcrypto;
  setGunzipFallback((bytes) => new Uint8Array(gunzipSync(Buffer.from(bytes))));
});

const params = { salt: vector.salt, iterations: vector.iterations };

describe('syncCrypto', () => {
  it('derives the key id the Python side produced', async () => {
    const { keyId } = await deriveKey(vector.passphrase, params);
    expect(keyId).toBe(vector.key_id);
  });

  it('checks the verifier, and rejects a wrong passphrase', async () => {
    const good = await deriveKey(vector.passphrase, params);
    expect(await checkVerifier(good.key, vector.verifier)).toBe(true);
    const bad = await deriveKey('WRONG-PASS', params);
    expect(bad.keyId).not.toBe(vector.key_id);
    expect(await checkVerifier(bad.key, vector.verifier)).toBe(false);
  });

  const sealed = (payload, extra = {}) => ({
    payload, encrypted: true, kind: vector.kind, record_id: vector.record_id, ...extra,
  });

  it('decrypts the encrypted payload in its own space', async () => {
    const { key } = await deriveKey(vector.passphrase, params);
    const doc = await decodeRecord(sealed(vector.payload_encrypted), key, { space: vector.space });
    expect(doc).toEqual(vector.doc);
  });

  // Version 1 never shipped and names no space, so opening it would let a
  // server replay a record across spaces: any other version is refused.
  it('refuses an envelope that is not version 2', async () => {
    const { key } = await deriveKey(vector.passphrase, params);
    const v1 = JSON.stringify({ ...JSON.parse(vector.payload_encrypted), v: 1 });
    await expect(decodeRecord(sealed(v1), key, { space: vector.space })).rejects.toThrow(SyncCryptoError);
    const verifierV1 = JSON.stringify({ ...JSON.parse(vector.verifier), v: 1 });
    expect(await checkVerifier(key, verifierV1)).toBe(false);
  });

  it('writes version 2 into the test vector', () => {
    expect(JSON.parse(vector.payload_encrypted).v).toBe(2);
    expect(JSON.parse(vector.verifier).v).toBe(2);
  });

  it('needs the space the page asked for, not the one the server echoes', async () => {
    const { key } = await deriveKey(vector.passphrase, params);
    await expect(decodeRecord(sealed(vector.payload_encrypted, { space: vector.space }), key))
      .rejects.toThrow(SyncCryptoError);
    await expect(decodeRecord(sealed(vector.payload_encrypted, { space: vector.space }), key, { space: '' }))
      .rejects.toThrow(SyncCryptoError);
  });

  it('fails when the record is replayed in another space', async () => {
    const { key } = await deriveKey(vector.passphrase, params);
    await expect(decodeRecord(sealed(vector.payload_encrypted), key, { space: 'work' })).rejects.toThrow();
    // The space the page asked for wins over the one the server echoes.
    await expect(decodeRecord(sealed(vector.payload_encrypted, { space: vector.space }), key, { space: 'work' }))
      .rejects.toThrow();
  });

  it('refuses a plain record once a key is unlocked', async () => {
    const { key } = await deriveKey(vector.passphrase, params);
    await expect(decodeRecord(
      { payload: vector.payload_plain, encrypted: false, kind: vector.kind, record_id: vector.record_id }, key,
    )).rejects.toThrow();
  });

  it('decodes the plain payload', async () => {
    const doc = await decodeRecord(
      { payload: vector.payload_plain, encrypted: false, kind: vector.kind, record_id: vector.record_id }, null);
    expect(doc).toEqual(vector.doc);
  });

  it('fails when the record id is swapped', async () => {
    const { key } = await deriveKey(vector.passphrase, params);
    await expect(decodeRecord(
      { payload: vector.payload_encrypted, encrypted: true, kind: vector.kind, record_id: 'chat-2' }, key,
      { space: vector.space },
    )).rejects.toThrow();
  });
});
