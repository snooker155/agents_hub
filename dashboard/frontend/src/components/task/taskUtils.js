/**
 * Task display helpers: executor label, money format, file tree, status and priority tables.
 */
import { RotateCw, User, Users, Workflow } from 'lucide-react';

// task.executor (tasks.models.Executor) is the source of truth for what is
// running a task; a task from before that field existed falls back to the
// compatibility assigned_agent_type string, read as a plain agent.
export const EXECUTOR_KIND_ICON = { agent: User, flow: Workflow, team: Users, loop: RotateCw };
export function executorLabel(task) {
  const ex = task?.executor;
  const kind = ex?.kind || 'agent';
  const id = ex?.id || task?.assigned_agent_type || '';
  return { kind, id, Icon: EXECUTOR_KIND_ICON[kind] || User };
}
export const fmtUsd = (n) => `$${(Number(n) || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const buildFileTree = (paths) => {
  const root = { type: 'dir', children: {} };
  (paths || []).forEach((rawPath) => {
    const cleanPath = String(rawPath || '').trim();
    if (!cleanPath) return;
    const parts = cleanPath.split('/').filter(Boolean);
    let node = root;
    parts.forEach((part, idx) => {
      const isFile = idx === parts.length - 1;
      if (!node.children[part]) {
        node.children[part] = isFile
          ? { type: 'file', name: part, path: parts.join('/') }
          : { type: 'dir', name: part, children: {} };
      }
      node = node.children[part];
    });
  });

  const toArray = (node, parentPath = '') => (
    Object.keys(node.children || {})
      .sort((a, b) => {
        const aNode = node.children[a];
        const bNode = node.children[b];
        if (aNode.type !== bNode.type) return aNode.type === 'dir' ? -1 : 1;
        return a.localeCompare(b);
      })
      .map((name) => {
        const child = node.children[name];
        const fullPath = parentPath ? `${parentPath}/${name}` : name;
        if (child.type === 'dir') {
          return { type: 'dir', name, path: fullPath, children: toArray(child, fullPath) };
        }
        return { type: 'file', name, path: child.path || fullPath };
      })
  );

  return toArray(root);
};

export const parentDirPaths = (filePath) => {
  const parts = String(filePath || '').split('/').filter(Boolean);
  const dirs = [];
  for (let i = 1; i < parts.length; i += 1) {
    dirs.push(parts.slice(0, i).join('/'));
  }
  return dirs;
};

export const isMarkdownPath = (p) => /\.(md|markdown|mdx)$/i.test(String(p || ''));

export const ALL_STATUSES = [
  { value: 'todo',        bg: 'bg-gray-100',    text: 'text-gray-600',   dot: 'bg-gray-400' },
  { value: 'ready',       bg: 'bg-blue-100',    text: 'text-blue-700',   dot: 'bg-blue-500' },
  { value: 'pending',     bg: 'bg-amber-100',   text: 'text-amber-700',  dot: 'bg-amber-500', readonly: true },
  { value: 'in_progress', bg: 'bg-yellow-100',  text: 'text-yellow-700', dot: 'bg-yellow-500' },
  { value: 'blocked',     bg: 'bg-red-100',     text: 'text-red-700',    dot: 'bg-red-500' },
  { value: 'awaiting_input', bg: 'bg-amber-100', text: 'text-amber-700',  dot: 'bg-amber-500', readonly: true },
  { value: 'awaiting_approval', bg: 'bg-amber-100', text: 'text-amber-700', dot: 'bg-amber-500', readonly: true },
  { value: 'stopped',     bg: 'bg-gray-100',    text: 'text-gray-500',   dot: 'bg-gray-400' },
  { value: 'resolved',    bg: 'bg-purple-100',  text: 'text-purple-700', dot: 'bg-purple-500' },
  { value: 'reviewing',   bg: 'bg-cyan-100',    text: 'text-cyan-700',   dot: 'bg-cyan-500',  readonly: true },
  { value: 'reviewed',    bg: 'bg-teal-100',    text: 'text-teal-700',   dot: 'bg-teal-500' },
  { value: 'done',        bg: 'bg-green-100',   text: 'text-green-700',  dot: 'bg-green-500' },
];

export const PRIORITIES = [
  { value: 'critical', color: 'text-red-600',    bg: 'bg-red-50',     border: 'border-red-200' },
  { value: 'high',     color: 'text-orange-600', bg: 'bg-orange-50',  border: 'border-orange-200' },
  { value: 'medium',   color: 'text-yellow-600', bg: 'bg-yellow-50',  border: 'border-yellow-200' },
  { value: 'low',      color: 'text-blue-500',   bg: 'bg-blue-50',    border: 'border-blue-200' },
];

export const statusCfg = (status) =>
  ALL_STATUSES.find(s => s.value === status) || ALL_STATUSES[0];

export const priorityCfg = (p) =>
  PRIORITIES.find(x => x.value === p);
