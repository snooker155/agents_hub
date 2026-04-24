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

function FlowNode({ data, selected }) {
  const tagStyle = TAG_STYLES[data.domain] || TAG_STYLES.general;
  const { isActive } = data;

  return (
    <div
      className={`relative w-[90px] rounded-xl border bg-white px-1.5 py-2 shadow-sm transition ${
        isActive
          ? 'border-cyan-500 shadow-lg shadow-cyan-200 ring-2 ring-cyan-400 ring-offset-1'
          : selected
          ? 'border-cyan-400 shadow-md shadow-cyan-100'
          : 'border-slate-200'
      }`}
    >
      {isActive && (
        <span className="absolute -right-1 -top-1 flex h-3 w-3">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cyan-400 opacity-75" />
          <span className="relative inline-flex h-3 w-3 rounded-full bg-cyan-500" />
        </span>
      )}
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

export default FlowNode;
