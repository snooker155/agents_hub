import { useNavigate } from 'react-router-dom';
import { GitBranch, Tag } from 'lucide-react';
import { REPO_ICONS, STATUS_CONFIG, TYPE_CONFIG } from './projectCardConfig';

/** One project in a grid: the Projects page and a workspace's Projects tab. */
export default function ProjectCard({ project }) {
  const navigate = useNavigate();
  const typeConf = TYPE_CONFIG[project.type] || TYPE_CONFIG.general;
  const statusConf = STATUS_CONFIG[project.status] || STATUS_CONFIG.active;
  const TypeIcon = typeConf.icon;
  const RepoIcon = REPO_ICONS[project.repo?.type] || GitBranch;
  const open = () => navigate(`/projects/${project.id}`);
  return (
    <div
      role="link"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => { if (e.key === 'Enter' && e.target === e.currentTarget) open(); }}
      data-testid={`project-card-${project.id}`}
      className="bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-xl hover:-translate-y-1 hover:border-indigo-400 hover:ring-4 hover:ring-indigo-100 transition-all duration-200 ease-out flex flex-col h-full cursor-pointer focus:outline-none focus-visible:ring-4 focus-visible:ring-indigo-200"
    >
      <div className="p-5 flex-1">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${typeConf.color}`}>
              <TypeIcon className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              {/* The title keeps its colour on hover: the card's lift and ring say it is clickable. */}
              <h3 className="font-semibold text-gray-900 truncate">{project.name}</h3>
              <span className="text-xs text-gray-400">{project.workspace}</span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusConf.color}`}>
              {statusConf.label}
            </span>
          </div>
        </div>

        {/* Always three lines tall, so every card is the same height whether or
            not it has a description, and a long one is clamped rather than grown. */}
        <p className="text-sm text-gray-500 mt-3 line-clamp-3 min-h-[3.75rem] leading-5">
          {project.description || ''}
        </p>

        {/* Indicators */}
        <div className="flex items-center gap-2 mt-3 flex-wrap">
          {project.repo?.type && project.repo.type !== 'none' && (
            <span className="flex items-center gap-1 text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
              <RepoIcon className="w-3 h-3" />
              {project.repo.type}
            </span>
          )}
          {project.tasks_count > 0 && (
            <span className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full">
              {project.tasks_count} task{project.tasks_count !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {/* Tags */}
        {project.tags?.length > 0 && (
          <div className="flex items-center gap-1 mt-2 flex-wrap">
            <Tag className="w-3 h-3 text-gray-400" />
            {project.tags.map(tag => (
              <span key={tag} className="text-xs text-gray-500 bg-gray-50 border border-gray-200 px-1.5 py-0.5 rounded">
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
