// The preset whose domains include the address's domain, for guessing from a
// typed username or from address (connectors/mail/presets.py has the same
// rule on the server). Kept apart from MailPresetPicker.jsx so that file
// exports only a component.
export function presetForAddress(presets, address) {
  const text = String(address || '');
  if (!text.includes('@')) return null;
  const domain = text.split('@').pop().trim().toLowerCase();
  if (!domain) return null;
  return (presets || []).find((p) => (p.domains || []).includes(domain)) || null;
}
