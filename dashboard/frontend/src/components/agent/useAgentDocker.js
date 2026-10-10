/**
 * The Docker tab's own state: the image and container lists for this agent, the
 * Dockerfile behind them, the two builds and the per-container actions.
 *
 * Lifted out of the page because none of it is of any interest to the other
 * twelve tabs, and because it loads only when the tab is opened — thirteen
 * tabs' worth of eager loading is what made this page slow to arrive.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  buildAgentImage, buildBaseImage, getContainerImages, getContainerLogs,
  getContainers, getDockerfile, removeContainer, stopContainerByName,
} from '../../api';
import { errorDetail } from '../toast';

export function useAgentDocker({ id, activeTab, t, toast }) {

  // Docker tab state
  const [dockerfileContent, setDockerfileContent] = useState('');
  const [dockerfileLoading, setDockerfileLoading] = useState(false);
  const [dockerImages, setDockerImages] = useState([]);
  const [dockerContainers, setDockerContainers] = useState([]);
  const [dockerLoading, setDockerLoading] = useState(false);
  // Why the images could not be read (Docker missing or not answering), so
  // the tab says so instead of calling every image "not built".
  const [dockerError, setDockerError] = useState('');
  const [imagesKnown, setImagesKnown] = useState(false);
  const [buildingBase, setBuildingBase] = useState(false);
  const [buildingAgent, setBuildingAgent] = useState(false);
  const [buildLog, setBuildLog] = useState('');
  const [buildError, setBuildError] = useState('');
  const [containerLogsName, setContainerLogsName] = useState(null);
  const [containerLogsText, setContainerLogsText] = useState('');
  const [containerLogsLoading, setContainerLogsLoading] = useState(false);
  const [dockerActionBusy, setDockerActionBusy] = useState({});

  // Which agent each load has completed for. The tab opening is a load too,
  // so its spinner is derived from this instead of set inside the effect.
  const [dataLoadedFor, setDataLoadedFor] = useState(null);
  const [fileLoadedFor, setFileLoadedFor] = useState(null);

  // The load itself sets no state until the answers arrive, so an effect may
  // call it; the manual refresh below raises the spinner first. Written as a
  // promise chain because the React Compiler lint treats an async function
  // called from an effect as a synchronous setState.
  const loadDockerData = useCallback(() => {
    // Each list on its own: one failing must not hide the other.
    return Promise.allSettled([
      getContainerImages(),
      getContainers(),
    ]).then(([imagesResult, containersResult]) => {
      if (imagesResult.status === 'fulfilled') {
        setDockerError('');
        setDockerImages(imagesResult.value.data?.images || []);
        setImagesKnown(true);
      } else {
        setDockerImages([]);
        setImagesKnown(false);
        setDockerError(errorDetail(imagesResult.reason) || t('agentDetails.errors.dockerData'));
      }
      if (containersResult.status === 'fulfilled') {
        const allContainers = containersResult.value.data?.containers || [];
        setDockerContainers(allContainers.filter(c => c.agent_id === id || c.name?.includes(id)));
      } else {
        setDockerContainers([]);
        if (imagesResult.status === 'fulfilled') {
          toast.error(t('agentDetails.errors.dockerData'), errorDetail(containersResult.reason));
        }
      }
      setDockerLoading(false);
      setDataLoadedFor(id);
    });
  }, [id, t, toast]);

  const fetchDockerData = useCallback(() => {
    setDockerLoading(true);
    setDockerError('');
    return loadDockerData();
  }, [loadDockerData]);

  const loadDockerfile = useCallback(() => getDockerfile(id)
    .then((resp) => setDockerfileContent(resp.data))
    .catch(() => setDockerfileContent(''))
    .then(() => {
      setDockerfileLoading(false);
      setFileLoadedFor(id);
    }), [id]);

  const fetchDockerfile = useCallback(() => {
    setDockerfileLoading(true);
    return loadDockerfile();
  }, [loadDockerfile]);

  const handleBuildBase = async () => {
    setBuildingBase(true);
    setBuildLog('');
    setBuildError('');
    try {
      const resp = await buildBaseImage({ no_cache: false });
      setBuildLog(resp.data?.log || t('agentDetails.buildComplete'));
    } catch (e) {
      setBuildError(e.response?.data?.detail || e.message || t('agentDetails.buildFailed'));
    } finally {
      setBuildingBase(false);
      fetchDockerData();
    }
  };

  const handleBuildAgent = async () => {
    setBuildingAgent(true);
    setBuildLog('');
    setBuildError('');
    try {
      const resp = await buildAgentImage(id, { no_cache: false });
      setBuildLog(resp.data?.log || t('agentDetails.buildComplete'));
    } catch (e) {
      setBuildError(e.response?.data?.detail || e.message || t('agentDetails.buildFailed'));
    } finally {
      setBuildingAgent(false);
      fetchDockerData();
    }
  };

  const handleShowContainerLogs = async (name) => {
    setContainerLogsName(name);
    setContainerLogsLoading(true);
    setContainerLogsText('');
    try {
      const resp = await getContainerLogs(name, 300);
      setContainerLogsText(typeof resp.data === 'string' ? resp.data : '');
    } catch (e) {
      setContainerLogsText(`[error: ${e.message}]`);
    }
    setContainerLogsLoading(false);
  };

  const handleStopContainer = async (name) => {
    setDockerActionBusy(b => ({ ...b, [name]: true }));
    try {
      await stopContainerByName(name);
      fetchDockerData();
    } catch (e) {
      toast.error(t('agentDetails.errors.stopContainer'), errorDetail(e));
    }
    setDockerActionBusy(b => ({ ...b, [name]: false }));
  };

  const handleRemoveContainer = async (name) => {
    setDockerActionBusy(b => ({ ...b, [name]: true }));
    try {
      await removeContainer(name);
      fetchDockerData();
    } catch (e) {
      toast.error(t('agentDetails.errors.removeContainer'), errorDetail(e));
    }
    setDockerActionBusy(b => ({ ...b, [name]: false }));
  };

  // Load the images, the containers and the Dockerfile when the tab opens.
  // Load Docker data + Dockerfile when the docker tab opens
  useEffect(() => {
    if (activeTab !== 'docker') return;
    loadDockerData();
    loadDockerfile();
  }, [activeTab, loadDockerData, loadDockerfile]);

  const opening = activeTab === 'docker';
  const dockerLoadingNow = dockerLoading || (opening && dataLoadedFor !== id);
  const dockerfileLoadingNow = dockerfileLoading || (opening && fileLoadedFor !== id);

  return {
    dockerfileContent, dockerfileLoading: dockerfileLoadingNow, dockerImages, dockerContainers,
    dockerLoading: dockerLoadingNow,
    dockerError, imagesKnown,
    buildingBase, buildingAgent, buildLog, buildError,
    containerLogsName, setContainerLogsName, containerLogsText, containerLogsLoading,
    dockerActionBusy,
    fetchDockerData, fetchDockerfile, handleBuildBase, handleBuildAgent,
    handleShowContainerLogs, handleStopContainer, handleRemoveContainer,
  };
}

export default useAgentDocker;
