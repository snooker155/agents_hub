// The letter and colour each artifact operation gets in a chat's file list.
// Its own module so the components that draw it stay component-only modules,
// which is what fast refresh needs.
export const ARTIFACT_OP_META = {
  add: { label: 'A', cls: 'bg-emerald-100 text-emerald-700' },
  modify: { label: 'M', cls: 'bg-amber-100 text-amber-700' },
  delete: { label: 'D', cls: 'bg-red-100 text-red-700' },
};
