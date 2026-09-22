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
  const [buildingBase, setBuildingBase] = useState(false);
  const [buildingAgent, setBuildingAgent] = useState(false);
  const [buildLog, setBuildLog] = useState('');
  const [buildError, setBuildError] = useState('');
  const [containerLogsName, setContainerLogsName] = useState(null);
  const [containerLogsText, setContainerLogsText] = useState('');
  const [containerLogsLoading, setContainerLogsLoading] = useState(false);
  const [dockerActionBusy, setDockerActionBusy] = useState({});

  const fetchDockerData = useCallback(async () => {
    setDockerLoading(true);
    try {
      const [imagesResp, containersResp] = await Promise.all([
        getContainerImages(),
        getContainers(),
      ]);
      setDockerImages(imagesResp.data?.images || []);
      const allContainers = containersResp.data?.containers || [];
      setDockerContainers(allContainers.filter(c => c.agent_id === id || c.name?.includes(id)));
    } catch (e) {
      toast.error(t('agentDetails.errors.dockerData'), errorDetail(e));
    }
    setDockerLoading(false);
  }, [id, t, toast]);

  const fetchDockerfile = useCallback(async () => {
    setDockerfileLoading(true);
    try {
      const resp = await getDockerfile(id);
      setDockerfileContent(typeof resp.data === 'string' ? resp.data : resp.data);
    } catch {
      setDockerfileContent('');
    }
    setDockerfileLoading(false);
  }, [id]);

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
    fetchDockerData();
    fetchDockerfile();
  }, [activeTab, fetchDockerData, fetchDockerfile]);

  return {
    dockerfileContent, dockerfileLoading, dockerImages, dockerContainers, dockerLoading,
    buildingBase, buildingAgent, buildLog, buildError,
    containerLogsName, setContainerLogsName, containerLogsText, containerLogsLoading,
    dockerActionBusy,
    fetchDockerData, fetchDockerfile, handleBuildBase, handleBuildAgent,
    handleShowContainerLogs, handleStopContainer, handleRemoveContainer,
  };
}

export default useAgentDocker;
