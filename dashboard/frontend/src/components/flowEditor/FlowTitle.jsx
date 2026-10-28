import InlineEdit from '../InlineEdit';
import { updateFlow } from '../../api/flows';
import { useI18n } from '../../i18n';
import { errorDetail, useToast } from '../toast';

// The flow's name in a page header, renamed in place the way a task's title
// is. Only the name is saved, so a canvas with unsaved edits keeps them as
// they are; `onRenamed` puts the new name into the page's own flow.
export default function FlowTitle({ flow, onRenamed }) {
  const { t } = useI18n();
  const toast = useToast();
  const save = async (value) => {
    const name = String(value || '').trim();
    if (!name || name === flow.name) return;
    try {
      await updateFlow(flow.id, { name });
      onRenamed(name);
    } catch (error) {
      toast.error(t('flowEditor.renameFailed'), errorDetail(error));
    }
  };
  return <InlineEdit value={flow.name || ''} onSave={save} className="text-base font-bold" />;
}
