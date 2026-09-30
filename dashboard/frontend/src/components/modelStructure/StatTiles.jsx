import { useI18n } from '../../i18n';
import { formatBytes, formatCount } from './graph';

// The compact facts under the page header: shape, size and memory.

function Tile({ label, value, hint }) {
  return (
    <div className="min-w-0 rounded-lg border border-gray-200 bg-white px-3 py-2">
      <div className="truncate text-[11px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className="truncate text-sm font-semibold text-gray-900" title={typeof value === 'string' ? value : undefined}>{value}</div>
      {hint && <div className="truncate text-[11px] text-gray-400">{hint}</div>}
    </div>
  );
}

const present = (v) => v !== null && v !== undefined && v !== '' && !(typeof v === 'number' && !Number.isFinite(v));

export default function StatTiles({ model = {}, memory = {} }) {
  const { t } = useI18n();
  const na = t('modelStructure.unknown');
  const count = (v) => (present(v) ? formatCount(v) : na);
  const bytes = (v) => (present(v) ? formatBytes(v) : na);
  const plain = (v) => (present(v) ? String(v) : na);
  const heads = present(model.heads) && present(model.kv_heads) && model.kv_heads !== model.heads
    ? t('modelStructure.stats.headsKv', { heads: model.heads, kv: model.kv_heads })
    : plain(model.heads);

  const tiles = [
    ['architecture', plain(model.architecture)],
    ['parameters', count(model.parameters)],
    ['layers', plain(model.layers)],
    ['hiddenSize', plain(model.hidden_size)],
    ['heads', heads],
    ['context', count(model.context_length)],
    ['quantization', plain(model.quantization || model.dtype)],
    ['fileSize', bytes(model.file_size_bytes)],
    ['weights', bytes(memory.weights_bytes)],
    ['kvCache', bytes(memory.kv_cache_bytes_at_context),
      present(memory.kv_cache_bytes_per_token)
        ? t('modelStructure.stats.kvCacheHint', { perToken: formatBytes(memory.kv_cache_bytes_per_token) })
        : null],
  ];
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5" data-testid="stat-tiles">
      {tiles.map(([key, value, hint]) => (
        <Tile key={key} label={t(`modelStructure.stats.${key}`)} value={value} hint={hint} />
      ))}
    </div>
  );
}
