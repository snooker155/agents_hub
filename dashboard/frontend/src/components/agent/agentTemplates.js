/** A template's name and description in the interface language, falling
 *  back to the English text the backend sends (agents/templates/<id>.md). */
export function templateText(t, template) {
  return {
    name: t(`agentTemplates.items.${template.id}.name`, { defaultValue: template.name }),
    description: t(`agentTemplates.items.${template.id}.description`, { defaultValue: template.description }),
  };
}
