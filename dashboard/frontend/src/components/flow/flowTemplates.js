/** A flow template's name and description in the interface language,
 *  falling back to the English text the backend sends. */
export function flowTemplateText(t, template) {
  return {
    name: t(`flowTemplates.items.${template.id}.name`, { defaultValue: template.name }),
    description: t(`flowTemplates.items.${template.id}.description`, { defaultValue: template.description }),
  };
}
