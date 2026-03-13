import React from 'react';
import { Handle, Position } from 'reactflow';

const TAG_STYLES = {
  management: 'bg-cyan-50 text-cyan-700',
  analysis: 'bg-amber-50 text-amber-700',
  design: 'bg-violet-50 text-violet-700',
  development: 'bg-emerald-50 text-emerald-700',
  testing: 'bg-rose-50 text-rose-700',
  operations: 'bg-orange-50 text-orange-700',
  automation: 'bg-slate-100 text-slate-600',
  general: 'bg-slate-100 text-slate-600',
};

function FactoryNode({ data, selected }) {
  const tagStyle = TAG_STYLES[data.domain] || TAG_STYLES.general;

  return (
    <div
      className={`w-[90px] rounded-xl border bg-white px-1.5 py-2 shadow-sm transition ${
        selected ? 'border-cyan-400 shadow-md shadow-cyan-100' : 'border-slate-200'
      }`}
    >
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-2 !border-white !bg-cyan-500" />
      <div className={`mb-1 block truncate rounded px-1 py-0.5 text-[7px] font-bold uppercase tracking-wide ${tagStyle}`}>
        {data.domain || 'general'}
      </div>
      <div className="text-[9px] font-semibold leading-tight text-slate-900 line-clamp-2">
        {data.label}
      </div>
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-2 !border-white !bg-cyan-500" />
    </div>
  );
}

export default FactoryNode;
