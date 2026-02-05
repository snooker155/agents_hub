import React from 'react';
import { Play, Activity } from 'lucide-react';

const ExperimentCard = ({ experiment, onRun, metrics, isRunning }) => {
  return (
    <div className="card h-100 border-0 shadow-sm">
      <div className="card-body">
        <div className="d-flex align-items-center justify-content-between">
          <h5 className="card-title text-primary mb-0">{experiment.name}</h5>
          {isRunning && (
            <span className="badge bg-success d-flex align-items-center"><Activity size={14} className="me-1"/>Running</span>
          )}
        </div>
        <p className="card-text text-muted small mb-2">{experiment.path}</p>
        {experiment.description && (
          <p className="text-muted" style={{fontSize: '0.95rem'}}>{experiment.description}</p>
        )}

        {metrics && (
          <div className="mb-3 small">
            <div className="text-secondary">Latest outcome (DB snapshot):</div>
            <div className="d-flex flex-wrap gap-3">
              <span>Obs: <strong>{metrics.n_obs}</strong></span>
              <span>Avg TPT: <strong>{metrics.avg_tpt?.toFixed?.(2) ?? metrics.avg_tpt} Mbps</strong></span>
              <span>P95 BLER: <strong>{((metrics.p95_bler ?? 0) * 100).toFixed(2)}%</strong></span>
            </div>
          </div>
        )}
        <div className="d-flex flex-wrap gap-2">
          {experiment.scripts.map((script) => (
            <button
              key={script}
              className="btn btn-sm btn-outline-primary d-flex align-items-center"
              onClick={() => onRun(script, experiment.configs)}
            >
              <Play size={12} className="me-1" fill="currentColor" />
              {script.split('/').pop()}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};

export default ExperimentCard;
