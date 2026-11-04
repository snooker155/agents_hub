/**
 * Files tab of a task: one list of its files, with a preview pane.
 */
import { useState } from 'react';
import { ChevronDown, ChevronLeft, ChevronRight, Code2, Eye, FileText, Folder, FolderOpen } from 'lucide-react';
import { getTaskFileRawUrl } from '../../api';
import TaskFilesCard from '../files/TaskFilesCard';
import MarkdownRenderer from '../MarkdownRenderer';
import PageLoader from '../PageLoader';
import { isMarkdownPath, parentDirPaths } from './taskUtils';
import { useI18n } from '../../i18n';
import { PHONE_SOURCE_EDGE, usePhone } from '../../lib/phone';
import { FULL_SCREEN_CLS, useFullScreen } from '../../lib/fullScreen';
import FullScreenButton from '../FullScreenButton';

// One list for every file of the task: the header with the Add button and
// the files attached to it, then the tree of the files in its working
// directory. No split into input and changed files.
export function TaskFilesTab({ task, onTaskChanged, expandedFolders, fileContentError, fileContentLoading, fileTree, id, loadFileContent, mdViewMode, pdfViewMode, selectedFileContent, selectedFileIsPdf, selectedFilePath, selectedFileSize, setExpandedFolders, setMdViewMode, setPdfViewMode, toggleFolder, workspaceFiles }) {
  const { t } = useI18n();
  // On a phone the tab is the list alone, and a picked file opens in its
  // place, with an arrow back to the list.
  const phone = usePhone();
  const [phoneFileOpen, setPhoneFileOpen] = useState(false);
  const fileOpen = phone && phoneFileOpen;
  // The open file over the whole screen (its bar's button, Escape back).
  const [fullFile, toggleFullFile] = useFullScreen();
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
          setPhoneFileOpen(true);
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
      {!fileOpen && <TaskFilesCard task={task} onChanged={onTaskChanged} bare />}
      {workspaceFiles.length ? (
        // One block, as tall as its content (the page scrolls, not the tree
        // or the preview): a narrow list, a divider, the file.
        <div className={`grid grid-cols-1 lg:grid-cols-12 border border-gray-200 rounded-lg overflow-hidden ${fileOpen ? '' : 'mt-4'}`}>
          {/* Tree */}
          <div className={`lg:col-span-3 p-2 border-b lg:border-b-0 lg:border-r border-gray-200 ${fileOpen ? 'hidden' : ''}`}>
            {renderFileNodes(fileTree)}
          </div>
          {/* Preview */}
          <div className={fullFile ? FULL_SCREEN_CLS : `lg:col-span-9 min-w-0 flex flex-col ${phone && !fileOpen ? 'hidden' : ''}`}>
            <div className="px-4 py-2 border-b border-gray-200 bg-gray-50 flex flex-wrap items-center justify-between gap-2 shrink-0">
              {fileOpen && (
                <button type="button" onClick={() => setPhoneFileOpen(false)} title={t('common.back')} aria-label={t('common.back')}
                  className="-ml-2 p-1.5 rounded-lg text-gray-500 hover:bg-white shrink-0">
                  <ChevronLeft className="w-5 h-5" />
                </button>
              )}
              <div className="min-w-[8rem] flex-1">
                <div className="hidden sm:block text-xs text-gray-500">{t('taskDetails.selectedFile')}</div>
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
                {selectedFilePath && <FullScreenButton on={fullFile} onToggle={toggleFullFile} />}
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
            {/* Switching files keeps the last file in view, dimmed, until the
                next one arrives: swapping it for the loader and back changed
                the panel's height twice, and the whole page jumped with it.
                The loader shows only before the first file is in. */}
            <div className={`${selectedFileIsPdf && pdfViewMode === 'render' ? '' : `p-4 ${PHONE_SOURCE_EDGE}`} ${fullFile ? 'flex-1 min-h-0 overflow-auto' : ''} overflow-x-auto transition-opacity ${
              fileContentLoading ? 'opacity-50' : ''}`} aria-busy={fileContentLoading || undefined} data-testid="file-preview">
              {fileContentLoading && !selectedFileContent && !selectedFileIsPdf ? (
                <PageLoader size="sm" label={t('taskDetails.loadingFileContent')} />
              ) : fileContentError ? (
                <p className="text-sm text-red-600 p-4">{fileContentError}</p>
              ) : selectedFilePath && selectedFileIsPdf && pdfViewMode === 'render' ? (
                <iframe
                  title={selectedFilePath}
                  src={getTaskFileRawUrl(id, selectedFilePath)}
                  className="w-full h-[80vh] border-0 block"
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
      ) : !task?.file_ids?.length && (
        <p className="text-sm text-gray-400 italic mt-3">{t('taskDetails.noFiles')}</p>
      )}
    </div>
  );
}
