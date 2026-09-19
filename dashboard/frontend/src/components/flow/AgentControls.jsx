import React, { useState } from 'react';
import { runFactoryAgent } from '../../api';
import { useI18n } from '../../i18n';

function AgentControls({ workspace }) {
  const { t } = useI18n();
  const [description, setDescription] = useState('');

  const runAgent = async (agent, action = null) => {
    if (!workspace) {
      alert(t('flowAgentControls.selectWorkspaceFirst'));
      return;
    }
    try {
      await runFactoryAgent({
        agent,
        action,
        description: agent === 'graph' || agent === 'pm' ? description : null,
        workspace
      });
      alert(`Started ${agent} ${action || ''}`);
    } catch {
      alert(t('flowAgentControls.startFailed'));
    }
  };

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-medium text-gray-900 border-b pb-2">{t('flowAgentControls.agentControls')}</h3>
      <div className="space-y-2">
        <textarea
          placeholder={t('flowAgentControls.projectDescription')}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="w-full border border-gray-300 rounded-md px-3 py-2 focus:ring-indigo-500 focus:border-indigo-500"
          rows={3}
        />
        <button
          onClick={() => runAgent('graph')}
          className="w-full bg-indigo-600 text-white px-4 py-2 rounded-md hover:bg-indigo-700 transition-colors font-medium"
        >
          {t('flowAgentControls.runFullGraph')}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2 mt-4">
        <button onClick={() => runAgent('pm', 'intake')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">{t('flowAgentControls.pmIntake')}</button>
        <button onClick={() => runAgent('ba', 'generate')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">{t('flowAgentControls.baGenerate')}</button>
        <button onClick={() => runAgent('sd', 'generate')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">{t('flowAgentControls.sdGenerate')}</button>
        <button onClick={() => runAgent('tl', 'split')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">{t('flowAgentControls.tlSplitTasks')}</button>
        <button onClick={() => runAgent('be')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">{t('flowAgentControls.runBeDev')}</button>
        <button onClick={() => runAgent('fe')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">{t('flowAgentControls.runFeDev')}</button>
        <button onClick={() => runAgent('qa')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">{t('flowAgentControls.runQa')}</button>
        <button onClick={() => runAgent('ops')} className="text-sm border border-gray-300 rounded px-2 py-1 hover:bg-gray-50 transition-colors">{t('flowAgentControls.runDevops')}</button>
      </div>
    </div>
  );
}

export default AgentControls;
