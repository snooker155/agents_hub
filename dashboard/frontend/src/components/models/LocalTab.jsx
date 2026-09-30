import { useState } from 'react';
import OllamaSection from './OllamaSection';
import RuntimeSection from './RuntimeSection';
import ServingSection from './ServingSection';
import JobProgress from './JobProgress';
import { useLocalJobs, ACTIVE_JOB_STATUSES as ACTIVE } from './jobs';
import { pullOllamaModel, downloadRuntimeModel } from '../../api/localModels';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';

/**
 * Feature 5's Local tab: an external Ollama, the hub's own model runtime, and
 * the hub's own /v1 endpoint it serves back out. One shared job queue (pulls
 * and downloads both go through it) is polled once here and handed down, so
 * a finished job bumps a refresh key on whichever section started it instead
 * of each section polling its own copy.
 */
export default function LocalTab() {
  const { t } = useI18n();
  const toast = useToast();
  const { jobs, reload: reloadJobs } = useLocalJobs();

  // A job the service closed as resumable (a restart, a dropped connection)
  // is started again with what it remembers about itself: the runtime
  // continues a download from its .part file, Ollama keeps pulled layers.
  const resume = async (job) => {
    const meta = job.meta || {};
    try {
      if (job.kind === 'hf_download') await downloadRuntimeModel(meta.repo, meta.file, meta.revision || undefined);
      else await pullOllamaModel(meta.name || job.name);
      reloadJobs();
    } catch (err) {
      toast.error(t('localModels.jobs.resumeFailed'), errorDetail(err));
    }
  };
  const [ollamaRefreshKey, setOllamaRefreshKey] = useState(0);
  const [runtimeRefreshKey, setRuntimeRefreshKey] = useState(0);

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
      if (justFinished && job.kind === 'ollama_pull') setOllamaRefreshKey((k) => k + 1);
      else if (justFinished && job.kind === 'hf_download') setRuntimeRefreshKey((k) => k + 1);
    }
    setPrevJobs(jobs);
  }

  return (
    <div className="space-y-4">
      <OllamaSection refreshKey={ollamaRefreshKey} onJobStarted={reloadJobs} />
      <RuntimeSection refreshKey={runtimeRefreshKey} onJobStarted={reloadJobs} />
      <JobProgress jobs={jobs} onResume={resume} />
      <ServingSection />
    </div>
  );
}
