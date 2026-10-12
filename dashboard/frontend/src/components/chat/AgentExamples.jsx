import { getAgentExamples, getFlowExamples } from '../../api';
import { useI18n } from '../../i18n';
import { useChatPage } from './context';
import ExampleRequests from './ExampleRequests';
import fillComposer from './fillComposer';

/**
 * Example requests under the agent in an empty chat, in the UI language: the
 * agent's examples.yaml, or the "Good fits" bullets of its usage text when it
 * has none (GET /api/agents/{id}/examples); an agent with neither gets six
 * general ones. A click puts the example into the message box to be edited
 * and sent, it does not send it.
 */
export default function AgentExamples() {
  const { selectedAgent, setInput, t, textareaRef } = useChatPage();
  const { language } = useI18n();
  return (
    <ExampleRequests
      load={() => getAgentExamples(selectedAgent, language)}
      loadKey={selectedAgent ? `${selectedAgent}:${language}` : null}
      fallback={[1, 2, 3, 4, 5, 6].map((n) => t(`chat.examples.fallback${n}`))}
      onPick={(text) => fillComposer(text, setInput, textareaRef)}
    />
  );
}

/**
 * The same for a flow picked on the Chat page: the flow's own examples (a
 * template's, or written in the editor), else its first agent's
 * (GET /api/flows/{id}/examples). No general fallback: a flow with neither
 * keeps just its explanation.
 */
export function FlowExamples() {
  const { selectedFlow, setInput, textareaRef } = useChatPage();
  const { language } = useI18n();
  return (
    <ExampleRequests
      load={() => getFlowExamples(selectedFlow, language)}
      loadKey={selectedFlow ? `${selectedFlow}:${language}` : null}
      onPick={(text) => fillComposer(text, setInput, textareaRef)}
    />
  );
}
