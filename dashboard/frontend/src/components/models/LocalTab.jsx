import { useCallback, useMemo, useState } from 'react';
import RuntimeSection from './RuntimeSection';
import RuntimeUsage from './RuntimeUsage';
import RuntimeCache from './RuntimeCache';
import ImportModels from './ImportModels';
import VoicesCard from './VoicesCard';
import JobProgress from './JobProgress';
import { useLocalJobs, ACTIVE_JOB_STATUSES as ACTIVE } from './jobs';
import { downloadRuntimeModel, downloadRuntimeSpeechModel } from '../../api/localModels';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';

// Jobs whose end changes what the runtime section shows.
const RUNTIME_JOB_KINDS = new Set(['hf_download', 'hf_package', 'engine_install', 'ollama_import', 'lmstudio_import']);

/**
 * Feature 5's Local tab: the hub's own model runtime, which local models run
 * on (it can also take over what Ollama and LM Studio downloaded), and every
 * call it served, from any caller. The hub's own /v1 endpoint has a tab of
 * its own on the Models page. The runtime's job queue is polled once here and
 * handed down, so a finished job bumps the runtime section's refresh key
 * instead of it polling a copy of its own.
 */
export default function LocalTab() {
  const { t } = useI18n();
  const toast = useToast();
  const { jobs: allJobs, reload: reloadJobs } = useLocalJobs();
  // Ollama pulls started from older versions of this tab stay out of it.
  const jobs = useMemo(() => allJobs.filter((j) => j.kind !== 'ollama_pull'), [allJobs]);

  // A job the service closed as resumable (a restart, a dropped connection)
  // is started again with what it remembers about itself: the runtime
  // continues a download from its .part file.
  const resume = async (job) => {
    const meta = job.meta || {};
    try {
      if (job.kind === 'hf_download') await downloadRuntimeModel(meta.repo, meta.file, meta.revision || undefined);
      else if (job.kind === 'hf_package') await downloadRuntimeSpeechModel(meta.repo, meta.dest, meta.revision || undefined);
      reloadJobs();
    } catch (err) {
      toast.error(t('localModels.jobs.resumeFailed'), errorDetail(err));
    }
  };
  const [runtimeRefreshKey, setRuntimeRefreshKey] = useState(0);

  // The runtime's call counts come with its status: RuntimeSection polls it
  // and hands each answer up here for the usage card.
  const [usage, setUsage] = useState(null);
  const [usageOutdated, setUsageOutdated] = useState(false);
  const [usageReloadKey, setUsageReloadKey] = useState(0);
  const [usageLoading, setUsageLoading] = useState(false);
  // The runtime's models, for the recorded voices card (which cloning models
  // can speak in them, which models read for OpenVoice).
  const [runtimeModels, setRuntimeModels] = useState([]);
  const onStatus = useCallback((status) => {
    setRuntimeModels(status?.models || []);
    setUsage(status?.usage || null);
    setUsageOutdated(!!status?.usage_outdated);
    setUsageLoading(false);
  }, []);
  const reloadUsage = useCallback(() => {
    setUsageLoading(true);
    setUsageReloadKey((k) => k + 1);
  }, []);

  // Comparing against the previous render's job list (rather than reacting
  // in a useEffect) is the pattern React itself recommends for "adjusting
  // state when a prop changes": it runs during render, so the two bumps
  // below land in the same commit as the job list update instead of causing
  // an extra one. See https://react.dev/learn/you-might-not-need-an-effect.
  const [prevJobs, setPrevJobs] = useState(jobs);
  if (jobs !== prevJobs) {
    const prevStatus = new Map(prevJobs.map((j) => [j.id, j.status]));
    for (const job of jobs) {
      const was = prevStatus.get(job.id);
      const justFinished = was && ACTIVE.has(was) && job.status === 'done';
      if (justFinished && RUNTIME_JOB_KINDS.has(job.kind)) setRuntimeRefreshKey((k) => k + 1);
    }
    setPrevJobs(jobs);
  }

  return (
    <div className="space-y-4">
      <RuntimeSection
        refreshKey={runtimeRefreshKey}
        reloadKey={usageReloadKey}
        onJobStarted={reloadJobs}
        onStatus={onStatus}
        jobs={jobs}
      />
      <VoicesCard models={runtimeModels} />
      <RuntimeUsage
        data={usage}
        outdated={usageOutdated}
        loading={usageLoading}
        onRefresh={reloadUsage}
        onChange={setUsage}
      />
      <RuntimeCache usage={usage} />
      <ImportModels
        refreshKey={runtimeRefreshKey}
        onImported={() => setRuntimeRefreshKey((k) => k + 1)}
        onJobStarted={reloadJobs}
      />
      <JobProgress jobs={jobs} onResume={resume} />
    </div>
  );
}
