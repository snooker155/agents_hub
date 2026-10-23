import { useI18n } from '../../i18n';
import SearchSelect from '../SearchSelect';

const subtitleOf = (a) => [a.name && a.name !== a.id ? a.id : null, a.description].filter(Boolean).join(' · ');

/**
 * The agent field of a flow node: the same searchable picker as the task's
 * executor, a row per agent with its id and description under the name. An
 * agent id the catalog no longer has stays in the list, so the field still
 * shows what the node holds. Amber while nothing is picked.
 */
export default function AgentSelect({ agents, value, onChange, testId }) {
  const { t } = useI18n();
  const items = agents.map((a) => ({ id: a.id, name: a.name || a.id, subtitle: subtitleOf(a) }));
  if (value && !agents.some((a) => a.id === value)) items.unshift({ id: value, name: value });
  return (
    <SearchSelect
      testId={testId}
      items={items}
      value={value}
      onChange={onChange}
      invalid={!value}
      placeholder={t('flowEditor.chooseAgent')}
      searchPlaceholder={t('flowEditor.searchAgents')}
      noMatchText={t('flowEditor.noAgentMatch')}
      emptyText={t('flowEditor.noAgents')}
    />
  );
}
