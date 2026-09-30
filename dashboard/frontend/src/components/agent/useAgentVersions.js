/**
 * The definition's version history, shown beside the prompt editors on the
 * Config tab: what changed, the diff against what is live now, and rolling one
 * back.
 *
 * `onRolledBack` is the page's own reload: a rollback rewrites the definition,
 * so the editors above the history have to be re-read from the server.
 */
import { useCallback, useEffect, useState } from 'react';
import { getAgentVersionDiff, getAgentVersions, rollbackAgentVersion } from '../../api';
import { errorDetail } from '../toast';

export function useAgentVersions({ id, activeTab, onRolledBack, t }) {
  const [agentVersions, setAgentVersions] = useState([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [versionsError, setVersionsError] = useState('');
  const [openDiffVersion, setOpenDiffVersion] = useState(null);
  const [diffText, setDiffText] = useState({});
  const [diffLoading, setDiffLoading] = useState(null);
  const [rollbackConfirmVersion, setRollbackConfirmVersion] = useState(null);
  const [rollbackBusy, setRollbackBusy] = useState(false);
  const [rollbackError, setRollbackError] = useState('');

  // Load version history when the config tab opens
  const fetchVersions = useCallback(() => {
    if (!id) return;
    setVersionsLoading(true);
    setVersionsError('');
    getAgentVersions(id)
      .then(r => setAgentVersions(r.data?.versions || []))
      .catch(e => setVersionsError(errorDetail(e) || t('agentDetails.versions.errors.load')))
      .finally(() => setVersionsLoading(false));
  }, [id, t]);

  // On a microtask rather than straight from the effect body: `fetchVersions`
  // raises its loading flag as it starts, and a setState made synchronously
  // inside an effect costs an extra render pass for nothing.
  useEffect(() => {
    if (activeTab !== 'config') return undefined;
    let cancelled = false;
    queueMicrotask(() => { if (!cancelled) fetchVersions(); });
    return () => { cancelled = true; };
  }, [activeTab, fetchVersions]);

  const handleToggleDiff = (version) => {
    if (openDiffVersion === version) {
      setOpenDiffVersion(null);
      return;
    }
    setOpenDiffVersion(version);
    if (diffText[version] !== undefined) return;
    setDiffLoading(version);
    getAgentVersionDiff(id, version, 'current')
      .then(r => setDiffText(prev => ({ ...prev, [version]: r.data?.diff || {} })))
      .catch(e => setDiffText(prev => ({ ...prev, [version]: { error: errorDetail(e) || t('agentDetails.versions.errors.diff') } })))
      .finally(() => setDiffLoading(null));
  };

  const handleRollback = (version) => {
    setRollbackBusy(true);
    setRollbackError('');
    rollbackAgentVersion(id, version)
      .then(() => {
        setRollbackConfirmVersion(null);
        setOpenDiffVersion(null);
        setDiffText({});
        fetchVersions();
        onRolledBack();
      })
      .catch(e => setRollbackError(errorDetail(e) || t('agentDetails.versions.errors.rollback')))
      .finally(() => setRollbackBusy(false));
  };


  return {
    agentVersions, versionsLoading, versionsError,
    openDiffVersion, diffText, diffLoading,
    rollbackConfirmVersion, setRollbackConfirmVersion, rollbackBusy,
    rollbackError, setRollbackError,
    fetchVersions, handleToggleDiff, handleRollback,
  };
}

export default useAgentVersions;
