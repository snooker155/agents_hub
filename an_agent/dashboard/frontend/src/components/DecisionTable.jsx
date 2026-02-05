import React from 'react';

const DecisionTable = ({ decisions }) => {
  return (
    <div className="card border-0 shadow-sm">
      <div className="card-header bg-white border-0 py-3 d-flex justify-content-between align-items-center">
        <h5 className="card-title mb-0">Recent Decisions</h5>
      </div>
      <div className="card-body p-0" style={{ maxHeight: '400px', overflowY: 'auto' }}>
        <table className="table table-sm table-hover mb-0">
          <thead className="table-light sticky-top">
            <tr>
              <th>Time</th>
              <th>Action</th>
              <th>Passed</th>
              <th>Lat (ms)</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {decisions.map((d, i) => (
              <tr key={i}>
                <td>{new Date(d.ts_ms).toLocaleTimeString()}</td>
                <td>
                  <span className="badge bg-secondary">{d.action_type}</span>
                </td>
                <td>{d.constraints_passed ? '✅' : '❌'}</td>
                <td>{d.latency_ms.toFixed(1)}</td>
                <td className="small text-muted text-truncate" style={{ maxWidth: '300px' }}>
                  {d.rationale}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default DecisionTable;
