import { Loader, X } from 'lucide-react';
import { useAgentPage } from './context';
import PageLoader from '../PageLoader';

/**
 * The page's own overlay: a container's logs. Starting a resident instance now
 * opens `StartInstanceModal` directly from the page header, and an instance's
 * own logs live on its Instance Detail page, so this is the only thing left
 * that is 'opened on top of whichever tab you were on' rather than part of one.
 */
export default function AgentModals() {
  const {
    containerLogsLoading, containerLogsName, containerLogsText, setContainerLogsName,
  } = useAgentPage();
  return (
    <>
      {/* Container logs modal */}
      {containerLogsName && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-950 rounded-xl shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-gray-800">
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
              <span className="text-gray-200 text-sm font-semibold font-mono">{containerLogsName}</span>
              <button onClick={() => setContainerLogsName(null)} className="text-gray-500 hover:text-gray-300">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-auto p-5">
              {containerLogsLoading ? (
                <PageLoader size="sm" />
              ) : (
                <pre className="text-xs text-green-400 whitespace-pre-wrap break-words leading-5">{containerLogsText || '(no output)'}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
