// A data class says how sensitive a workspace's work is (common/data_class.py).
export default {
  title: 'Data class',
  label: 'Data class of this workspace',
  explainer: 'How sensitive this workspace\'s work is. It decides which linked nodes (other machines lending their models or sandbox) may see the work. An agent can make it stricter for itself, never looser.',
  public: 'Public',
  internal: 'Internal',
  confidential: 'Confidential',
  desc: {
    public: 'Any node may see the work.',
    internal: 'Only nodes the hub administrator trusts. This is the default.',
    confidential: 'Only attested nodes (hardware its owner cannot read). None exist yet, so this work never leaves this hub.',
  },
  needs: 'Needs a node that is at least: {{trust}}.',
  saved: 'Saved',
  errors: { load: 'Could not load the data class.', save: 'Could not save the data class.' },
};
