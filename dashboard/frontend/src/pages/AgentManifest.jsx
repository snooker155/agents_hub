import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileCode, Play, AlertCircle, CheckCircle2, Info } from 'lucide-react';
import { applyAgentManifest } from '../api';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
const AgentManifest = () => {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [yaml, setYaml] = useState(`kind: Agent
metadata:
  name: example-agent
spec:
  displayName: Example Agent
  domain: development
  description: "A new agent deployed via YAML manifest"
  type: langchain
  capacity: 2
  tools:
    - read_file
    - write_file
    - list_files
  defaultParams:
    temperature: 0.7
`);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  const handleApply = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSuccess(false);

    try {
      await applyAgentManifest({ yaml });
      setSuccess(true);
      setTimeout(() => {
        navigate('/agents');
      }, 1500);
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={FileCode}
        title={t('agentManifest.applyAgentManifest')}
        description={t('agentManifest.deployNewAgentsOrUpdate')}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <form onSubmit={handleApply} className="space-y-4">
            <div className="bg-gray-900 rounded-lg overflow-hidden shadow-lg border border-gray-800">
              <div className="flex items-center justify-between px-4 py-2 bg-gray-800 text-gray-400 text-xs border-b border-gray-700">
                <span>{t('agentManifest.manifestYaml')}</span>
                <span className="flex items-center">
                  <span className="w-2 h-2 rounded-full bg-green-500 mr-2"></span>
                  YAML
                </span>
              </div>
              <textarea
                value={yaml}
                onChange={(e) => setYaml(e.target.value)}
                className="w-full h-[500px] bg-gray-900 text-indigo-300 p-4 focus:outline-none resize-none"
                spellCheck="false"
              />
            </div>

            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-4">
                {error && (
                  <div className="flex items-center text-red-500 text-sm">
                    <AlertCircle className="w-4 h-4 mr-1" />
                    {error}
                  </div>
                )}
                {success && (
                  <div className="flex items-center text-green-500 text-sm font-bold">
                    <CheckCircle2 className="w-4 h-4 mr-1" />
                    {t('agentManifest.manifestAppliedSuccessfully')}
                  </div>
                )}
              </div>
              <button
                type="submit"
                disabled={loading}
                className={`flex items-center px-6 py-2 rounded-md text-white font-bold transition-all shadow-lg ${
                  loading ? 'bg-gray-400 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700 hover:scale-105 active:scale-95'
                }`}
              >
                {loading ? 'Applying...' : (
                  <>
                    <Play className="w-4 h-4 mr-2" />
                    {t('agentManifest.applyChanges')}
                  </>
                )}
              </button>
            </div>
          </form>
        </div>

        <div className="space-y-6">
          <div className="bg-white p-6 rounded-lg border border-gray-100 shadow-sm">
            <h3 className="font-bold text-gray-800 mb-4 flex items-center">
              <Info className="w-4 h-4 mr-2 text-blue-500" />
              {t('agentManifest.manifestSpec')}
            </h3>
            <div className="space-y-4 text-xs">
              <div>
                <span className=" text-indigo-600">{t('agentManifest.kind')}</span>
                <p className="text-gray-500">{t('agentManifest.mustBeAgent')}</p>
              </div>
              <div>
                <span className=" text-indigo-600">{t('agentManifest.metadataName')}</span>
                <p className="text-gray-500">{t('agentManifest.uniqueIdentifierForTheAgent')}</p>
              </div>
              <div>
                <span className=" text-indigo-600">{t('agentManifest.specDomain')}</span>
                <p className="text-gray-500">{t('agentManifest.agentDomainHint')}</p>
              </div>
              <div>
                <span className=" text-indigo-600">{t('agentManifest.specCapacity')}</span>
                <p className="text-gray-500">{t('agentManifest.maxConcurrentRunsAllowed')}</p>
              </div>
              <div>
                <span className=" text-indigo-600">{t('agentManifest.specTools')}</span>
                <p className="text-gray-500">{t('agentManifest.listOfToolIdsExposed')}</p>
              </div>
            </div>
          </div>

          <div className="bg-indigo-50 p-6 rounded-lg border border-indigo-100">
            <h4 className="text-indigo-800 font-bold text-sm mb-2">{t('agentManifest.proTip')}</h4>
            <p className="text-indigo-700 text-xs leading-relaxed">
              {t('agentManifest.useManifestsToQuicklySpin')}
            </p>
          </div>
        </div>
      </div>
    </PageContainer>
  );
};

export default AgentManifest;
