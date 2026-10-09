/**
 * Files tab of a task: changed-file tree with a preview pane.
 */
import { ChevronDown, ChevronRight, Code2, Eye, FileText, Folder, FolderOpen } from 'lucide-react';
import { getTaskFileRawUrl } from '../../api';
import MarkdownRenderer from '../MarkdownRenderer';
import PageLoader from '../PageLoader';
import { isMarkdownPath, parentDirPaths } from './taskUtils';
import { useI18n } from '../../i18n';

export function TaskFilesTab({ expandedFolders, fileContentError, fileContentLoading, fileTree, id, loadFileContent, mdViewMode, pdfViewMode, selectedFileContent, selectedFileIsPdf, selectedFilePath, selectedFileSize, setExpandedFolders, setMdViewMode, setPdfViewMode, toggleFolder, workspaceFiles }) {
  const { t } = useI18n();
  const renderFileNodes = (nodes, depth = 0) => nodes.map((node) => {
    if (node.type === 'dir') {
      const open = expandedFolders.has(node.path);
      return (
        <div key={node.path}>
          <button
            type="button"
            onClick={() => toggleFolder(node.path)}
            className="w-full flex items-center gap-1.5 px-2 py-1 text-sm text-gray-700 hover:bg-gray-50 rounded text-left"
            style={{ paddingLeft: `${depth * 14 + 8}px` }}
            title={node.path}
          >
            {open ? <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-400 shrink-0" />}
            {open ? <FolderOpen className="w-4 h-4 text-amber-500 shrink-0" /> : <Folder className="w-4 h-4 text-amber-500 shrink-0" />}
            <span className="truncate">{node.name}</span>
          </button>
          {open && node.children?.length > 0 && renderFileNodes(node.children, depth + 1)}
        </div>
      );
    }
    const isSelected = node.path === selectedFilePath;
    return (
      <button
        key={node.path}
        type="button"
        onClick={() => {
          setExpandedFolders((prev) => {
            const next = new Set(prev);
            parentDirPaths(node.path).forEach((dir) => next.add(dir));
            return next;
          });
          loadFileContent(node.path);
        }}
        className={`w-full flex items-center gap-1.5 px-2 py-1 text-sm rounded text-left ${
          isSelected ? 'bg-indigo-50 text-indigo-700' : 'text-gray-700 hover:bg-gray-50'
        }`}
        style={{ paddingLeft: `${depth * 14 + 28}px` }}
        title={node.path}
      >
        <FileText className="w-4 h-4 text-gray-400 shrink-0" />
        <span className="truncate">{node.name}</span>
      </button>
    );
  });

  return (
    <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-6">
      <h3 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
        <Folder className="w-5 h-5" /> {t('taskDetails.filesChangedByTask')}
      </h3>
      {workspaceFiles.length ? (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 h-[500px]">
          {/* Tree */}
          <div className="lg:col-span-4 border border-gray-200 rounded-lg p-2 overflow-y-auto min-h-0">
            {renderFileNodes(fileTree)}
          </div>
          {/* Preview */}
          <div className="lg:col-span-8 border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0">
            <div className="px-4 py-2 border-b bg-gray-50 flex items-center justify-between gap-2 shrink-0">
              <div className="min-w-0">
                <div className="text-xs text-gray-500">{t('taskDetails.selectedFile')}</div>
                <div className="text-sm text-gray-700 truncate flex items-center gap-2">
                  <span className="truncate">{selectedFilePath || '-'}</span>
                  {selectedFileIsPdf && (
                    <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded bg-red-100 text-red-600 shrink-0">
                      <FileText className="w-2.5 h-2.5" /> {t('taskDetails.pdf')}
                    </span>
                  )}
                </div>
                {selectedFileSize > 0 && (
                  <div className="text-xs text-gray-400 mt-0.5">{t('taskDetails.bytes', { count: selectedFileSize })}</div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {selectedFileIsPdf && (
                  <div className="flex rounded-lg border border-gray-200 overflow-hidden text-xs font-semibold">
                    <button
                      type="button"
                      onClick={() => setPdfViewMode('render')}
                      className={`px-2.5 py-1 transition-colors ${pdfViewMode === 'render' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                    >
                      {t('taskDetails.render')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setPdfViewMode('text')}
                      className={`px-2.5 py-1 transition-colors border-l border-gray-200 ${pdfViewMode === 'text' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                    >
                      {t('taskDetails.text')}
                    </button>
                  </div>
                )}
                {!selectedFileIsPdf && isMarkdownPath(selectedFilePath) && (
                  <div className="flex rounded-lg border border-gray-200 overflow-hidden text-xs font-semibold">
                    <button
                      type="button"
                      onClick={() => setMdViewMode('rendered')}
                      className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors ${mdViewMode === 'rendered' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                    >
                      <Eye className="w-3.5 h-3.5" /> {t('taskDetails.rendered')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setMdViewMode('raw')}
                      className={`inline-flex items-center gap-1 px-2.5 py-1 transition-colors border-l border-gray-200 ${mdViewMode === 'raw' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                    >
                      <Code2 className="w-3.5 h-3.5" /> {t('taskDetails.raw')}
                    </button>
                  </div>
                )}
              </div>
            </div>
            <div className={`${selectedFileIsPdf && pdfViewMode === 'render' ? '' : 'p-4'} flex-1 min-h-0 overflow-auto`}>
              {fileContentLoading ? (
                <PageLoader size="sm" label={t('taskDetails.loadingFileContent')} />
              ) : fileContentError ? (
                <p className="text-sm text-red-600 p-4">{fileContentError}</p>
              ) : selectedFilePath && selectedFileIsPdf && pdfViewMode === 'render' ? (
                <iframe
                  title={selectedFilePath}
                  src={getTaskFileRawUrl(id, selectedFilePath)}
                  className="w-full h-full border-0"
                />
              ) : selectedFilePath && isMarkdownPath(selectedFilePath) && mdViewMode === 'rendered' ? (
                <MarkdownRenderer content={selectedFileContent} />
              ) : selectedFilePath ? (
                <pre className="text-xs text-gray-800 whitespace-pre-wrap break-words">{selectedFileContent}</pre>
              ) : (
                <p className="text-sm text-gray-500">{t('taskDetails.selectAFileToPreview')}</p>
              )}
            </div>
          </div>
        </div>
      ) : (
        <p className="text-sm text-gray-400 italic">{t('taskDetails.noFilesWereCreatedOr')}</p>
      )}
    </div>
  );
}
