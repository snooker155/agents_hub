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
  const hashes = snapshot.files && typeof snapshot.files === 'object' ? snapshot.files : null;
  if (hashes && Object.keys(hashes).length) {
    // A repository skill: one line per file with the start of its hash, so a
    // changed script shows as a changed line.
    lines.push(`${t('skillsCatalog.history.fieldFiles')}:`);
    Object.keys(hashes).sort().forEach((name) => lines.push(`${name} ${String(hashes[name]).slice(0, 12)}`));
  } else if ((snapshot.resources || []).length) {
    lines.push(`${t('skillsCatalog.history.fieldFiles')}: ${snapshot.resources.join(', ')}`);
  }
  return lines.join('\n');
}
