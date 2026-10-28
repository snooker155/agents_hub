// A tool's name, description and group in the interface language
// (i18n/locales/*/toolNames.js and toolPicker.categories). A tool the
// dictionary does not know, an MCP or a custom one, keeps the text the
// backend sends for it.

const titleCase = (s) => String(s || '').replace(/_/g, ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());

export const toolLabel = (t, id, fallback) => t(`toolNames.${id}.label`, { defaultValue: fallback || id });

export const toolDescription = (t, id, fallback) => t(`toolNames.${id}.description`, { defaultValue: fallback || '' });

export const toolCategoryLabel = (t, category) => {
  const c = category || 'other';
  return t(`toolPicker.categories.${c}`, { defaultValue: titleCase(c) });
};
