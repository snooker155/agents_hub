/**
 * What a message box carries besides its text, shared by every box a message
 * is written in (the Chat page, a flow's chat, an instance): the paperclip
 * with its menu (a file from the computer, a file of the workspace, a hub
 * record), what is attached so far above the box, and the two pickers. The
 * state lives with the box (chat/useChatComposerInput.js does the work).
 */
import { CheckCircle, FolderOpen, Loader, Paperclip, Upload, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useI18n } from '../../i18n';
import ContextEntityPicker from '../ContextEntityPicker';
import WorkspaceFilePicker from '../files/WorkspaceFilePicker';
import { COMPOSER_ATTACH_CLS } from './composerStyles';

/** The paperclip at the start of the line and the menu it opens above it. */
export function AttachButton({ open, onOpenChange, disabled = false, contextKinds = [], onFiles, onWorkspaceFiles, onKind }) {
  const { t } = useI18n();
  const item = 'w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50';
  return (
    <div className="relative flex-shrink-0">
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        className={COMPOSER_ATTACH_CLS}
        title={t('chat.attachMenuTitle')}
        disabled={disabled}
      >
        <Paperclip className="w-4 h-4" />
      </button>
      {open && (
        <>
          {/* Click-away layer: a menu anchored above the box has no other way
              to close without stealing focus. */}
          <div className="fixed inset-0 z-40" onClick={() => onOpenChange(false)} />
          <div className="absolute bottom-10 left-0 z-50 w-60 max-h-80 overflow-y-auto bg-white border border-gray-200 rounded-xl shadow-lg py-1">
            <button type="button" onClick={() => { onOpenChange(false); onFiles(); }} className={item}>
              <Upload className="w-4 h-4 text-gray-400" />
              {t('chat.attachFiles')}
            </button>
            <button type="button" onClick={() => { onOpenChange(false); onWorkspaceFiles(); }} className={item}>
              <FolderOpen className="w-4 h-4 text-gray-400" />
              {t('files.chat.fromWorkspace')}
            </button>
            {contextKinds.length > 0 && (
              <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wide text-gray-400 border-t border-gray-100 mt-1">
                {t('chat.attachFromHub')}
              </div>
            )}
            {contextKinds.map((k) => (
              <button key={k.kind} type="button" onClick={() => { onOpenChange(false); onKind(k.kind); }} className={item}>
                <span aria-hidden="true">{k.icon}</span>
                {t(`chat.entityKind.${k.kind}`, { defaultValue: k.noun })}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/** The hub records attached to the next message, as chips. */
export function PendingReferences({ references, onRemove, disabled = false }) {
  const { t } = useI18n();
  if (!references.length) return null;
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5">
      {references.map((ref) => (
        <span
          key={`${ref.kind}:${ref.id}`}
          className="inline-flex items-center gap-1.5 max-w-full rounded-full border border-indigo-200
            bg-indigo-50 px-2 py-1 text-[11px] text-indigo-700"
          title={t(`chat.entityKind.${ref.kind}`, { defaultValue: ref.kind })}
        >
          <span aria-hidden="true">{ref.icon}</span>
          {ref.url ? (
            <Link to={ref.url} className="truncate max-w-[14rem] font-medium hover:underline">
              {ref.label || ref.id}
            </Link>
          ) : (
            <span className="truncate max-w-[14rem] font-medium">{ref.label || ref.id}</span>
          )}
          <button
            type="button"
            onClick={() => onRemove(ref.kind, ref.id)}
            className="text-indigo-400 hover:text-red-600 flex-shrink-0"
            title={t('chat.removeAttachment')}
            disabled={disabled}
          >
            <X className="w-3 h-3" />
          </button>
        </span>
      ))}
    </div>
  );
}

/**
 * The files attached to the next message: each with its size and whether it
 * is kept in the workspace (or already is one of its files).
 */
export function PendingAttachments({ attachments, workspace, onToggleStore, onRemove, disabled = false }) {
  const { t } = useI18n();
  if (!attachments.length) return null;
  return (
    <div className="mb-2 space-y-2">
      {attachments.map((att) => (
        <div key={att.id} className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg border border-gray-200 bg-gray-50">
          <div className="min-w-0">
            <div className="text-xs text-gray-700 font-medium truncate">{att.filename}</div>
            <div className="text-[11px] text-gray-500">{t('chat.bytes', { count: att.size })}</div>
          </div>
          <div className="flex items-center gap-3">
            {att.file_id ? (
              <Link
                to={`/files?file=${encodeURIComponent(att.file_id)}`}
                className="flex items-center gap-1 text-xs text-emerald-700 hover:underline"
                data-testid="attachment-saved"
              >
                {att.from_workspace ? <FolderOpen className="w-3.5 h-3.5" /> : <CheckCircle className="w-3.5 h-3.5" />}
                {att.from_workspace ? t('files.chat.workspaceFile') : t('files.chat.saved')}
              </Link>
            ) : att.saving ? (
              <span className="flex items-center gap-1 text-xs text-gray-500">
                <Loader className="w-3.5 h-3.5 animate-spin" /> {t('files.chat.saving')}
              </span>
            ) : (
              <label className={`flex items-center gap-1.5 text-xs ${workspace ? 'text-gray-600' : 'text-gray-400'}`}>
                <input
                  type="checkbox"
                  checked={Boolean(att.store_to_workspace)}
                  onChange={(e) => onToggleStore(att.id, e.target.checked)}
                  disabled={!workspace || disabled}
                />
                {t('chat.storeInWorkspace')}
              </label>
            )}
            <button
              type="button"
              onClick={() => onRemove(att.id)}
              className="text-gray-400 hover:text-red-600"
              title={t('chat.removeAttachment')}
              disabled={disabled}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      ))}
      {!workspace && (
        <p className="text-[11px] text-amber-600">{t('chat.selectAWorkspaceIfYou')}</p>
      )}
    </div>
  );
}

/** The two pickers the menu opens: workspace files, and hub records of one kind. */
export function AttachPickers({
  workspace, projectId = null, filePickerOpen, onCloseFilePicker, attachments, onAddWorkspaceFiles,
  pickerKind, onClosePicker, references, onAddReferences,
}) {
  return (
    <>
      {filePickerOpen && (
        <WorkspaceFilePicker
          workspace={workspace}
          excludeIds={attachments.map((a) => a.file_id).filter(Boolean)}
          uploadSource="chat"
          onPick={(records) => { onCloseFilePicker(); onAddWorkspaceFiles(records); }}
          onClose={onCloseFilePicker}
        />
      )}
      {pickerKind && (
        <ContextEntityPicker
          initialKind={pickerKind}
          workspace={workspace}
          projectId={projectId}
          selected={references}
          onAdd={onAddReferences}
          onClose={onClosePicker}
        />
      )}
    </>
  );
}
