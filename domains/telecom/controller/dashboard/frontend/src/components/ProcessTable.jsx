import React from 'react';
import { Square } from 'lucide-react';

const ProcessTable = ({ processes, onStop }) => {
  return (
    <div className="card border-0 shadow-sm">
      <div className="card-header bg-white border-0 py-3">
        <h5 className="card-title mb-0">Running Processes</h5>
      </div>
      <div className="card-body p-0">
        <div className="table-responsive">
          <table className="table table-hover mb-0">
            <thead className="table-light">
              <tr>
                <th>PID</th>
                <th>Name</th>
                <th>Command</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {processes.map((p) => (
                <tr key={p.pid}>
                  <td className="align-middle">{p.pid}</td>
                  <td className="align-middle">
                    <span className="badge bg-info text-dark">{p.name}</span>
                  </td>
                  <td className="align-middle">
                    <code className="small">{p.cmd}</code>
                  </td>
                  <td className="align-middle">
                    <button
                      className="btn btn-sm btn-danger"
                      onClick={() => onStop(p.pid)}
                      title="Stop Process"
                    >
                      <Square size={14} fill="white" />
                    </button>
                  </td>
                </tr>
              ))}
              {processes.length === 0 && (
                <tr>
                  <td colSpan="4" className="text-center py-4 text-muted">
                    No active processes
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default ProcessTable;
