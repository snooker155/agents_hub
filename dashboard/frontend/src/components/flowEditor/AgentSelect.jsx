import { useI18n } from '../../i18n';
import SearchSelect from '../SearchSelect';

const subtitleOf = (a) => [a.name && a.name !== a.id ? a.id : null, a.description].filter(Boolean).join(' · ');

/**
 * The agent field of a flow node, a team and the like: the same searchable
 * picker as the task's executor, a row per agent with its id and description
 * under the name. An agent id the catalog no longer has stays in the list, so
 * the field still shows what it holds. Amber while nothing is picked, unless
 * `invalid` says otherwise. `head` is an extra row put first, such as a
 * default that stands for no agent in particular. `singleLine` as in
 * SearchSelect.
 */
export default function AgentSelect({ agents, value, onChange, testId, placeholder, invalid, head, singleLine }) {
  const { t } = useI18n();
  const items = agents.map((a) => ({ id: a.id, name: a.name || a.id, subtitle: subtitleOf(a) }));
  if (value && !agents.some((a) => a.id === value)) items.unshift({ id: value, name: value });
  if (head) items.unshift(head);
  return (
    <SearchSelect
      testId={testId}
      items={items}
      value={value}
      onChange={onChange}
      invalid={invalid ?? !value}
      singleLine={singleLine}
      placeholder={placeholder || t('flowEditor.chooseAgent')}
      searchPlaceholder={t('flowEditor.searchAgents')}
      noMatchText={t('flowEditor.noAgentMatch')}
      emptyText={t('flowEditor.noAgents')}
    />
  );
}
