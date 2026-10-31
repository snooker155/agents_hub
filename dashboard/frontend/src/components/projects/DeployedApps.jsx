/**
 * The apps the hub runs for projects (docs/project-deployments.md), on the
 * Projects page: each opens on its project, a running one also at its share
 * address. Hidden while there are none.
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listDeployedApps } from '../../api';
import { useWorkspace } from '../workspace';
import { DeployStatusPill } from './DeployPanel';

export default function DeployedApps({ t }) {
  const { workspaceFilter } = useWorkspace();
  const workspace = workspaceFilter || undefined;
  const [apps, setApps] = useState([]);
  useEffect(() => {
    let cancelled = false;
    const load = () => listDeployedApps(workspace)
      .then(({ data }) => { if (!cancelled) setApps(data.items || []); })
      .catch(() => {});
    load();
    const timer = setInterval(load, 10000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [workspace]);
  if (!apps.length) return null;
  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden" data-testid="deployed-apps">
      <div className="px-4 py-2 border-b border-gray-100 text-xs font-semibold text-gray-500 uppercase tracking-wider">
        {t('projectDeploy.appsTitle')}
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-gray-100">
          {apps.map((app) => (
            <tr key={app.id}>
              <td className="px-4 py-2 font-medium text-gray-800">
                <Link to={`/projects/${app.project_id}`} className="hover:text-indigo-600">{app.name || app.project_id}</Link>
              </td>
              <td className="px-4 py-2"><DeployStatusPill status={app.status} t={t} /></td>
              <td className="px-4 py-2 text-xs text-gray-500">{t(`projectDeploy.modes.${app.mode}`)} · {(app.services || []).length} {t('projectDeploy.servicesCount')}</td>
              <td className="px-4 py-2 text-xs text-gray-500">{app.workspace}</td>
              <td className="px-4 py-2 text-right text-xs">
                {app.status === 'running' && app.links?.path ? (
                  <a href={app.links.path} target="_blank" rel="noopener noreferrer" className="text-indigo-600 hover:text-indigo-800">{t('projectDeploy.open')}</a>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
