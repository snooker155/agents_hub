import React, { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { getWorkspace, getWorkspaceFilesByName } from '../api';
import { ChevronLeft, Folder, FileText } from 'lucide-react';

const WorkspaceDetails = () => {
  const { name } = useParams();
  const navigate = useNavigate();
  const [ws, setWs] = useState(null);
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    setLoading(true);
    try {
      const info = await getWorkspace(name);
      setWs(info.data);
      const f = await getWorkspaceFilesByName(name);
      setFiles(f.data.files || []);
    } catch (e) {
      console.error('Failed to load workspace', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); const i = setInterval(fetchData, 5000); return () => clearInterval(i); }, [name]);

  if (loading) return <div className="text-center py-10">Loading workspace...</div>;
  if (!ws) return <div className="text-center py-10">Workspace not found</div>;

  return (
    <div>
      <Link to="/workspaces" className="flex items-center text-indigo-600 hover:text-indigo-900 mb-6">
        <ChevronLeft className="w-4 h-4 mr-1" /> Back to Workspaces
      </Link>

      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center">
          <Folder className="w-7 h-7 text-gray-700 mr-2" />
          <div>
            <h2 className="text-2xl font-bold text-gray-900">{ws.name}</h2>
            <div className="text-sm text-gray-500 font-mono truncate max-w-2xl">{ws.path}</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-4">Allocated Tasks</h3>
          {ws.tasks && ws.tasks.length ? (
            <div className="space-y-3">
              {ws.tasks.map(t => (
                <div
                  key={t.id}
                  className="p-3 border rounded hover:bg-gray-50 cursor-pointer"
                  onClick={() => navigate(`/tasks/${t.id}`)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/tasks/${t.id}`); }}
                >
                  <div className="flex justify-between items-center">
                    <div>
                      <Link to={`/tasks/${t.id}`} onClick={(e) => e.stopPropagation()} className="font-medium text-indigo-700 hover:underline">{t.title}</Link>
                      <div className="text-xs text-gray-500">Status: {t.status}</div>
                    </div>
                    <div className="w-40 bg-gray-200 rounded-full h-2.5">
                      <div className="bg-indigo-600 h-2.5 rounded-full" style={{ width: `${t.progress}%` }}></div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-gray-500 text-sm">No tasks bound to this workspace.</p>
          )}
        </div>

        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-lg font-bold mb-4 flex items-center"><FileText className="w-5 h-5 mr-2"/>Files</h3>
          {files.length ? (
            <ul className="text-sm text-gray-700 max-h-96 overflow-auto list-disc pl-6">
              {files.map(f => <li key={f} className="truncate">{f}</li>)}
            </ul>
          ) : (
            <p className="text-gray-500 text-sm">No files found.</p>
          )}
        </div>
      </div>
    </div>
  );
};

export default WorkspaceDetails;
