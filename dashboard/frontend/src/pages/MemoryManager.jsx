import { useState, useEffect, useCallback } from 'react';
import { Database, Cpu, Users, RefreshCw, FileSearch } from 'lucide-react';
import { getSharedMemories } from '../api';
import { useWorkspace } from '../components/workspace';
import EntityChat from '../components/EntityChat';
import { usePageChat } from '../components/pageChat/pageChat';
import { ChatColumn, ChatToggle, FILL_COLUMN, useChatColumn } from '../components/ChatColumn';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { useToast, errorDetail } from '../components/toast';
// The tabs and panels this page composes. See components/memoryManager/ for
// the pieces: each tab or panel is its own file, split out of what used to be
// one 2300-line page, the way components/chat/ is split out of Chat.jsx.
import { PoolsTab } from '../components/memoryManager/PoolsTab';
import { AgentsTab } from '../components/memoryManager/AgentsTab';
import { RagPipelineTab } from '../components/memoryManager/RagPipelineTab';
import { IndexedFilesTab } from '../components/memoryManager/IndexedFilesTab';
import { useMemoryChatDescriptor } from '../components/memoryManager/useMemoryChatDescriptor';

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------
const TABS = [
  { id: 'pools',   labelKey: 'memoryManager.tabs.pools',   icon: Database   },
  { id: 'agents',  labelKey: 'memoryManager.tabs.agents',  icon: Users      },
  { id: 'rag',     labelKey: 'memoryManager.tabs.rag',     icon: Cpu        },
  { id: 'indexed', labelKey: 'memoryManager.tabs.indexed', icon: FileSearch },
];

export default function MemoryManager() {
  const { t } = useI18n();
  const toast = useToast();
  const { workspaceFilter } = useWorkspace();
  const [activeTab, setActiveTab] = useState('pools');
  const [memories, setMemories] = useState([]);
  const [loading, setLoading] = useState(true);
  // Lifted out of PoolsTab: the chat beside it is bound to whichever pool is
  // open, and the toggle for the column lives in the page header.
  const [selectedPoolId, setSelectedPoolId] = useState(null);
  const chat = useChatColumn(true);
  const chatVisible = chat.open && activeTab === 'pools';

  const fetchMemories = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await getSharedMemories(workspaceFilter);
      setMemories(resp.data);
    } catch (e) {
      toast.error(t('memoryManager.errors.loadMemories'), errorDetail(e));
    } finally { setLoading(false); }
  }, [workspaceFilter, t, toast]);

  useEffect(() => { fetchMemories(); }, [fetchMemories]);

  // The pool chat, drawn in the column beside the pools or in the floating
  // panel. Registered only on the Pools tab: that is the only tab it is about.
  const memoryChat = useMemoryChatDescriptor(workspaceFilter, selectedPoolId, fetchMemories);
  usePageChat(activeTab === 'pools' ? memoryChat : null);

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={Database}
        title={t('memoryManager.sharedMemory')}
        description={t('memoryManager.manageAgentMemoryPoolsRag')}
        actions={<>
          {activeTab === 'pools' && (
            <ChatToggle open={chat.open} onToggle={chat.toggle}
                        label={t('memoryManager.askChat')} />
          )}
          <button onClick={fetchMemories} className="flex items-center gap-2 border border-gray-200 text-gray-600 px-3 py-2 rounded-lg hover:bg-gray-50 text-sm">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-indigo-500' : ''}`} /> {t('memoryManager.refresh')}
          </button>
        </>}
      />

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1">
        {TABS.map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                activeTab === tab.id ? 'bg-white text-indigo-600 shadow-sm' : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              <Icon className="w-4 h-4" />
              <span className="hidden sm:inline">{t(tab.labelKey)}</span>
            </button>
          );
        })}
      </div>

      {/* Tab content, with the Memory Agent beside the pools it is about. */}
      <div className={chatVisible ? chat.gridClass : ''}>
        <div className={chatVisible ? chat.mainClass : ''}>
          {activeTab === 'pools'   && <PoolsTab memories={memories} onRefresh={fetchMemories}
                                                workspaceFilter={workspaceFilter}
                                                onPoolSelected={setSelectedPoolId} />}
          {activeTab === 'agents'  && <AgentsTab memories={memories} workspaceFilter={workspaceFilter} />}
          {activeTab === 'rag'     && <RagPipelineTab memories={memories} workspaceFilter={workspaceFilter} />}
          {activeTab === 'indexed' && <IndexedFilesTab memories={memories} />}
        </div>

        {chatVisible && (
          <ChatColumn>
            <EntityChat {...memoryChat} {...FILL_COLUMN} />
          </ChatColumn>
        )}
      </div>
    </PageContainer>
  );
}
