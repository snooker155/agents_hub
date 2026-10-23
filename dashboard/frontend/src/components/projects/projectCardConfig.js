import {
  FolderGit2, Code2, BookOpen, FileText, GitBranch, Github, Gitlab,
} from 'lucide-react';

export const TYPE_CONFIG = {
  general:       { label: 'General',       icon: FolderGit2, color: 'bg-gray-100 text-gray-700' },
  code:          { label: 'Code',          icon: Code2,       color: 'bg-blue-100 text-blue-700' },
  research:      { label: 'Research',      icon: BookOpen,    color: 'bg-purple-100 text-purple-700' },
  documentation: { label: 'Docs',          icon: FileText,    color: 'bg-yellow-100 text-yellow-700' },
};

export const STATUS_CONFIG = {
  active:    { label: 'Active',    color: 'bg-green-100 text-green-700' },
  archived:  { label: 'Archived', color: 'bg-gray-100 text-gray-500' },
  completed: { label: 'Done',     color: 'bg-blue-100 text-blue-700' },
};

export const REPO_ICONS = {
  github:    Github,
  gitlab:    Gitlab,
  bitbucket: GitBranch,
  local:     FolderGit2,
};

/**
 * Fixed-width tracks, not breakpoints and not fluid ones. Breakpoints measure
 * the viewport while the grid may live in a column that narrows when a chat
 * opens, so a fixed column count rewrapped every card's text. A minimum track
 * width keeps every card the same shape wherever the grid sits: the Projects
 * page and a workspace's Projects tab alike.
 */
export const PROJECT_GRID = 'grid grid-cols-1 sm:grid-cols-[repeat(auto-fill,minmax(15.5rem,1fr))] gap-4 items-stretch';
