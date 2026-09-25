/** A version's content as the text the diff compares, labels in the UI language. */
export function snapshotText(snapshot, t) {
  if (!snapshot) return '';
  const lines = [
    `${t('skillsCatalog.history.fieldName')}: ${snapshot.name || ''}`,
    `${t('skillsCatalog.history.fieldWhen')}: ${snapshot.description || ''}`,
  ];
  if ((snapshot.tags || []).length) {
    lines.push(`${t('skillsCatalog.history.fieldTags')}: ${snapshot.tags.join(', ')}`);
  }
  if ((snapshot.steps || []).length) {
    lines.push(`${t('skillsCatalog.history.fieldSteps')}:`);
    snapshot.steps.forEach((step, i) => lines.push(`${i + 1}. ${step}`));
  }
  if (snapshot.body) {
    lines.push(`${t('skillsCatalog.history.fieldInstructions')}:`);
    lines.push(...String(snapshot.body).split('\n'));
  }
  if ((snapshot.resources || []).length) {
    lines.push(`${t('skillsCatalog.history.fieldFiles')}: ${snapshot.resources.join(', ')}`);
  }
  return lines.join('\n');
}
