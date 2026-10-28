import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronLeft, Lock, LockOpen, RefreshCw, Vault as VaultIcon } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';

import { getSyncRecord, listSyncKeys, listSyncRecords, listSyncSpaces } from '../api/syncVault';
import { PageContainer, PageHeader } from '../components/PageLayout';
import PageLoader from '../components/PageLoader';
import UnlockDialog from '../components/vault/UnlockDialog';
import RecordViewer from '../components/vault/viewers';
import { formatBytes, formatWhen } from '../components/vault/format';
import { useI18n } from '../i18n';
import { decodeRecord } from '../lib/syncCrypto';

const CONCURRENCY = 4;
const recKey = (r) => `${r.kind}/${r.record_id}`;

// Its own component so the page passes openRecord as a plain handler: the
// hooks linter cannot tell an inline arrow inside the list from a call made
// while rendering, and openRecord reads refs.
const RecordLink = ({ rec, onOpen, children }) => (
  <button type="button" onClick={() => onOpen(rec)} className="text-left text-indigo-700 hover:underline">{children}</button>
);

/**
 * Synced data: what a person pushed from their own hub. Plain spaces read
 * directly. An encrypted space stays sealed until the person types the
 * passphrase and consents; then it is decrypted here, in this tab, and the key
 * and every plaintext live in component state only (never in storage, never
 * sent back) until Lock or the tab closes.
 */
export default function Vault() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const selected = params.get('space') || '';

  const [spaces, setSpaces] = useState(null);
  const [keys, setKeys] = useState([]);
  const [error, setError] = useState('');
  const [records, setRecords] = useState([]);
  const [session, setSession] = useState(null); // { key, space }
  const [titles, setTitles] = useState({});
  const [progress, setProgress] = useState(null);
  const [asking, setAsking] = useState(false);
  const [open, setOpen] = useState(null); // { rec, doc, error }
  const generation = useRef(0);
  // The unlocked session as the record list's late answer sees it. Unlocking
  // decodes the titles of the records listed so far, and the list can land
  // after the unlock (a click flushes the effect that reloads it, so the
  // unlock sees an empty list): the arriving list then decodes its titles
  // itself, or every title would stay at the ellipsis.
  const sessionRef = useRef(null);
  // Which open click is current: Back or a second click supersedes a fetch
  // still in flight, the same way Lock does through `generation`.
  const opening = useRef(0);

  const space = (spaces || []).find((s) => s.space === selected) || null;
  const keyRow = space ? keys.find((k) => k.key_id === space.key_id) : null;
  const unlocked = Boolean(session && session.space === selected);

  const lock = useCallback(() => {
    generation.current += 1;
    sessionRef.current = null;
    setSession(null);
    setTitles({});
    setProgress(null);
    setOpen(null);
  }, []);

  const loadSpaces = useCallback(async () => {
    setError('');
    try {
      const [sp, ks] = await Promise.all([listSyncSpaces(), listSyncKeys()]);
      setSpaces(sp.data.spaces || []);
      setKeys(ks.data.keys || []);
    } catch {
      setError(t('vault.loadFailed'));
      setSpaces((cur) => cur || []);
    }
  }, [t]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- the loader sets state only after its await
  useEffect(() => { loadSpaces(); }, [loadSpaces]);

  // The space goes to decodeRecord from the page, not from the record the
  // server returns: a server replaying another space's record would echo
  // that space too, so only the page's own choice proves where it belongs.
  const decodeOne = useCallback(async (rec, key) => {
    const res = await getSyncRecord(selected, rec.kind, rec.record_id);
    return decodeRecord(res.data, key, { space: selected });
  }, [selected]);

  // Fetch each record and read its title, a few at a time. A newer lock or
  // selection bumps `generation`, and the stale run drops its results.
  const decodeTitles = useCallback(async (list, key) => {
    const gen = generation.current;
    const queue = list.slice();
    let done = 0;
    setProgress({ done: 0, total: list.length });
    const worker = async () => {
      while (queue.length && gen === generation.current) {
        const rec = queue.shift();
        let title = '';
        try { title = (await decodeOne(rec, key)).title || ''; } catch { title = ''; }
        if (gen !== generation.current) return;
        done += 1;
        setTitles((cur) => ({ ...cur, [recKey(rec)]: title }));
        setProgress({ done, total: list.length });
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, list.length) }, worker));
    if (gen === generation.current) setProgress(null);
  }, [decodeOne]);

  // Choosing another space drops any unlock and reloads its records.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- a new space resets what the old one showed
    lock();
    setRecords([]);
    if (!selected) return undefined;
    let alive = true;
    listSyncRecords(selected).then((res) => {
      if (!alive) return;
      const list = res.data.records || [];
      setRecords(list);
      const sp = (spaces || []).find((s) => s.space === selected);
      const unlockedHere = sessionRef.current && sessionRef.current.space === selected ? sessionRef.current : null;
      if (unlockedHere) decodeTitles(list, unlockedHere.key);
      else if (sp && !sp.encrypted) decodeTitles(list.filter((r) => !r.encrypted), null);
    }).catch(() => { if (alive) setError(t('vault.loadFailed')); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, spaces]);

  useEffect(() => lock, [lock]);

  const onUnlocked = ({ key }) => {
    setAsking(false);
    sessionRef.current = { key, space: selected };
    setSession(sessionRef.current);
    decodeTitles(records, key);
  };

  // A large record takes a while to fetch and decrypt. If Lock, a space
  // switch, Back or another click lands meanwhile, the late result is
  // dropped: writing it would put plaintext on a page that looks locked, or
  // show the old space's record under the new one.
  const openRecord = useCallback(async (rec) => {
    const gen = generation.current;
    opening.current += 1;
    const ticket = opening.current;
    const current = () => gen === generation.current && ticket === opening.current;
    setOpen({ rec, doc: null, error: '' });
    try {
      const doc = await decodeOne(rec, session?.key || null);
      if (current()) setOpen({ rec, doc, error: '' });
    } catch {
      if (current()) setOpen({ rec, doc: null, error: t('vault.decodeFailed') });
    }
  }, [decodeOne, session, t]);

  const closeRecord = useCallback(() => {
    opening.current += 1;
    setOpen(null);
  }, []);

  const canRead = (rec) => !rec.encrypted || unlocked;
  const titleOf = (rec) => {
    if (rec.encrypted && !unlocked) return t('vault.encryptedTitle');
    const v = titles[recKey(rec)];
    return v === undefined ? '…' : (v || t('vault.untitled'));
  };

  const grouped = records.reduce((acc, r) => { (acc[r.kind] = acc[r.kind] || []).push(r); return acc; }, {});
  const kindLabel = (k) => {
    const v = t(`vault.kinds.${k}`);
    return v === `vault.kinds.${k}` ? k : v;
  };

  if (spaces === null) return <PageLoader />;

  return (
    <PageContainer>
      <PageHeader
        icon={VaultIcon}
        title={t('vault.title')}
        description={t('vault.description')}
        actions={(
          <button onClick={loadSpaces} className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            <RefreshCw size={14} />{t('vault.refresh')}
          </button>
        )}
      />
      {error && <p role="alert" className="text-sm text-red-600 mb-3">{error}</p>}

      {unlocked && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 mb-4 text-sm bg-amber-50 border border-amber-200 rounded-lg text-amber-900" role="status">
          <LockOpen size={14} className="shrink-0" />
          <span className="font-medium">{t('vault.unlockedStrip')}</span>
          <span className="flex-1 min-w-[160px] text-amber-800">{t('vault.unlockedHint')}</span>
          <button onClick={lock} className="flex items-center gap-1 px-2 py-1 text-sm bg-white border border-amber-300 rounded-lg hover:bg-amber-100">
            <Lock size={13} />{t('vault.lock')}
          </button>
        </div>
      )}

      {!spaces.length ? (
        <p className="text-sm text-gray-500">{t('vault.noSpaces')}</p>
      ) : (
        <div className="grid md:grid-cols-[18rem_1fr] gap-4">
          <div>
            <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">{t('vault.spacesTitle')}</h2>
            <ul className="space-y-2">
              {spaces.map((s) => (
                <li key={s.space}>
                  <button
                    onClick={() => setParams({ space: s.space })}
                    aria-pressed={s.space === selected}
                    className={`w-full text-left rounded-lg border px-3 py-2 ${s.space === selected ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 bg-white hover:bg-gray-50'}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-gray-800 truncate">{s.space}</span>
                      {s.encrypted ? (
                        <span className="inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 shrink-0"><Lock size={10} />{t('vault.encrypted')}</span>
                      ) : (
                        <span className="text-[11px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 shrink-0">{t('vault.plain')}</span>
                      )}
                    </div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      {t('vault.recordCount', { count: s.counts?.total || 0 })} · {formatBytes(s.counts?.bytes)}
                    </div>
                    <div className="text-[11px] text-gray-400">
                      {t('vault.lastPush', { when: s.last_push_at ? formatWhen(s.last_push_at) : t('vault.never') })}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="min-w-0">
            {!space ? (
              <p className="text-sm text-gray-500">{t('vault.selectSpace')}</p>
            ) : open ? (
              <div>
                <button onClick={closeRecord} className="flex items-center gap-1 text-sm text-indigo-600 hover:underline mb-3">
                  <ChevronLeft size={14} />{t('vault.back')}
                </button>
                <h2 className="text-base font-semibold text-gray-900 mb-3 break-words">{(canRead(open.rec) && open.doc?.title) || titleOf(open.rec)}</h2>
                {open.error && <p role="alert" className="text-sm text-red-600">{open.error}</p>}
                {open.doc && canRead(open.rec) && <RecordViewer doc={open.doc} />}
              </div>
            ) : (
              <div>
                {space.encrypted && !unlocked && (
                  <div className="mb-3">
                    {keyRow ? (
                      <button onClick={() => setAsking(true)} className="inline-flex items-center gap-2 px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700">
                        <LockOpen size={14} />{t('vault.unlockToView')}
                      </button>
                    ) : <p className="text-sm text-gray-500">{t('vault.noKey')}</p>}
                  </div>
                )}
                {progress && <p className="text-xs text-gray-500 mb-2" role="status">{t('vault.progress', progress)}</p>}
                {!records.length ? (
                  <p className="text-sm text-gray-500">{t('vault.noRecords')}</p>
                ) : Object.keys(grouped).map((kind) => (
                  <section key={kind} className="mb-5">
                    <h3 className="text-sm font-semibold text-gray-700 mb-1">{kindLabel(kind)} <span className="text-gray-400 font-normal">({grouped[kind].length})</span></h3>
                    <ul className="divide-y divide-gray-100 border border-gray-200 rounded-lg bg-white">
                      {grouped[kind].map((r) => (
                        <li key={recKey(r)} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-2">
                          <span className="flex-1 min-w-[10rem] text-sm text-gray-800 break-words">
                            {canRead(r) ? (
                              <RecordLink rec={r} onOpen={openRecord}>{titleOf(r)}</RecordLink>
                            ) : <span className="text-gray-400 inline-flex items-center gap-1"><Lock size={11} />{titleOf(r)}</span>}
                          </span>
                          <span className="text-xs text-gray-500">{formatWhen(r.modified_at)}</span>
                          <span className="text-xs text-gray-400 w-16 text-right">{formatBytes(r.size)}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {asking && space && keyRow && (
        <UnlockDialog space={space.space} keyRow={keyRow} onClose={() => setAsking(false)} onUnlocked={onUnlocked} />
      )}
    </PageContainer>
  );
}
