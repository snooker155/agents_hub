/**
 * What a run log line says, in the page language. The saved log carries
 * English lines for text-only readers ("Completed Writer", "Flow 'x'
 * finished"); the Logs panel words each line from its type and fields
 * instead, and falls back to the saved text for a type it does not know.
 *
 * `label` is the name column (a node's name, or the flow / the summary),
 * `content` the status beside it, `output` what the output block shows: a
 * switch's output is the branch it chose in words (routeText), not its raw
 * `key=value → target` summary.
 */
import { rerunBubbleText, routeText } from './runFormat';

// The English output a stopped node is saved with; its status says it already.
const STOPPED_OUTPUT = 'Stopped by user';

export function logText(t, log, nodeLabels = {}) {
  const ty = log?.type || '';
  // A saved skip line names only its node: the flow's own name for it.
  const name = log?.agent_name || nodeLabels[log?.node_id] || log?.agent_id || log?.node_id || '';
  const L = (key, values) => t(`flowEditor.log.${key}`, values);
  switch (ty) {
    case 'flow_start':
      return {
        label: L('flow'),
        content: log.turn?.rerun ? rerunBubbleText(t, log.turn.rerun) : L('started'),
        output: '',
      };
    case 'flow_finish':
      return { label: L('flow'), content: L('finished'), output: '' };
    case 'flow_stopped':
      return { label: L('flow'), content: L('stopped'), output: '' };
    case 'flow_resume':
      return {
        label: L('flow'),
        content: log.rerun ? rerunBubbleText(t, log.rerun) : L('resumed'),
        output: '',
      };
    case 'flow_interrupt':
      return { label: L('flow'), content: L('waitingForPerson', { question: log.question || '' }), output: '' };
    case 'flow_error':
      return { label: L('flow'), content: L('failed'), output: log.content || '' };
    case 'flow_summary':
      return { label: L('summary'), content: '', output: log.output || '' };
    case 'agent_start':
      return { label: name || L('node'), content: L('running'), output: log.output || '' };
    case 'agent_finish':
      return {
        label: name || L('node'),
        content: log.route ? L('chose') : L('completed'),
        output: log.route ? routeText(t, log.route) : (log.output || ''),
      };
    case 'agent_error':
      return { label: name || L('node'), content: L('failed'), output: log.output || '' };
    case 'agent_stopped':
      return {
        label: name || L('node'),
        content: L('stopped'),
        output: log.output && log.output !== STOPPED_OUTPUT ? log.output : '',
      };
    case 'node_skip':
      return {
        label: name || L('node'),
        content: log.reason
          ? t(`flowEditor.skipReasons.${log.reason}`, { defaultValue: t('flowEditor.skipped') })
          : t('flowEditor.skipped'),
        output: '',
      };
    default:
      return { label: name || ty, content: log?.content || '', output: log?.output || '' };
  }
}
