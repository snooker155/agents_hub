// The model a run actually called, as recorded on the run, with its provider
// under it. Shared by the run lists (Runs page, Costs page Runs tab). A run
// that ended before its first model call has none; the cell shows a dash
// rather than the agent's configured model, which would be a guess.
export default function RunModelCell({ run }) {
  const model = (run?.model || '').trim();
  if (!model) return <span className="text-xs text-gray-400">—</span>;
  const provider = (run?.provider || '').trim();
  return (
    <div className="min-w-0" title={provider ? `${provider} · ${model}` : model}>
      <div className="text-xs font-mono truncate">{model}</div>
      {provider && <div className="text-[10px] text-gray-400 truncate">{provider}</div>}
    </div>
  );
}
