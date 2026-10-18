/**
 * The screens after the voice, each with a "not now" or a plain choice:
 * personal memory, the demo workspace. A screen whose answer is already in
 * place (the demo already there) says so and only asks to go on.
 */
import { useEffect, useState } from 'react';
import {
  Brain, BrainCircuit, LayoutGrid, Sparkles, SquareDashed,
} from 'lucide-react';
import { useI18n } from '../../../i18n';
import { setDemo } from '../../../api/demo';
import { getWorkspacePersonalMemory, updateWorkspacePersonalMemory } from '../../../api/workspaces';
import {
  ChoiceCard, ConfiguredFrame, DoneNote, ErrorLine, LaterButton, PrimaryButton, StepFrame,
} from '../ui';
import { errorText } from '../firstRunModel';

/** Runs one save, then moves on; keeps the error on screen if it fails. */
function useSave(next) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = async (fn) => {
    setBusy(true);
    setError('');
    try {
      await fn();
      next();
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

export function MemoryStep({ next }) {
  const { t } = useI18n();
  const { busy, error, run } = useSave(next);
  const [was, setWas] = useState(null);
  const [enabled, setEnabled] = useState(true);
  useEffect(() => {
    let live = true;
    getWorkspacePersonalMemory('default')
      .then(({ data }) => { if (live) { setWas(Boolean(data?.enabled)); setEnabled(Boolean(data?.enabled)); } })
      .catch(() => { if (live) setWas(true); });
    return () => { live = false; };
  }, []);

  const save = () => run(async () => {
    if (was !== null && enabled !== was) await updateWorkspacePersonalMemory('default', enabled);
  });

  return (
    <StepFrame
      icon={BrainCircuit}
      title={t('firstRun.memory.title')}
      subtitle={t('firstRun.memory.subtitle')}
      testId="first-run-memory"
      footer={<PrimaryButton onClick={save} busy={busy} disabled={was === null} testId="first-run-continue">{t('firstRun.continue')}</PrimaryButton>}
    >
      <div className="space-y-3">
        <ChoiceCard icon={Brain} title={t('firstRun.memory.on')} detail={t('firstRun.memory.onDetail')}
          selected={enabled} onClick={() => setEnabled(true)} testId="first-run-memory-on" />
        <ChoiceCard icon={SquareDashed} title={t('firstRun.memory.off')} detail={t('firstRun.memory.offDetail')}
          selected={!enabled} onClick={() => setEnabled(false)} testId="first-run-memory-off" />
      </div>
      <p className="mt-4 text-sm text-center text-gray-400">{t('firstRun.memory.later')}</p>
      <ErrorLine>{error}</ErrorLine>
    </StepFrame>
  );
}

export function DemoStep({ ctx, refreshCtx, next, configured }) {
  const { t } = useI18n();
  const [changing, setChanging] = useState(false);
  const { busy, error, run } = useSave(next);
  const [add, setAdd] = useState(true);
  const present = Boolean(ctx?.demo);

  const save = () => run(async () => {
    if (add && !present) {
      await setDemo(true);
      await refreshCtx();
    }
  });

  const was = configured?.demo;
  if (was && !changing) {
    return (
      <ConfiguredFrame icon={LayoutGrid} title={t('firstRun.demo.title')} subtitle={t('firstRun.demo.subtitle')}
        summary={was.enabled ? t('firstRun.demo.on') : t('firstRun.demo.off')}
        onContinue={() => next()} onChange={() => setChanging(true)} testId="first-run-demo" t={t} />
    );
  }

  return (
    <StepFrame
      icon={LayoutGrid}
      title={t('firstRun.demo.title')}
      subtitle={t('firstRun.demo.subtitle')}
      testId="first-run-demo"
      footer={<PrimaryButton onClick={present ? () => next() : save} busy={busy} testId="first-run-continue">{t('firstRun.continue')}</PrimaryButton>}
    >
      {present ? (
        <DoneNote title={t('firstRun.demo.present')} detail={t('firstRun.demo.presentDetail')} />
      ) : (
        <div className="space-y-3">
          <ChoiceCard icon={Sparkles} title={t('firstRun.demo.add')} detail={t('firstRun.demo.addDetail')}
            selected={add} onClick={() => setAdd(true)} testId="first-run-demo-add" />
          <ChoiceCard icon={SquareDashed} title={t('firstRun.demo.empty')} detail={t('firstRun.demo.emptyDetail')}
            selected={!add} onClick={() => setAdd(false)} testId="first-run-demo-empty" />
        </div>
      )}
      <ErrorLine>{error}</ErrorLine>
    </StepFrame>
  );
}
