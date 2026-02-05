import React from 'react';

const StatCard = ({ title, value, unit = '' }) => {
  return (
    <div className="card border-0 shadow-sm text-center h-100">
      <div className="card-body">
        <h6 className="text-muted">{title}</h6>
        <h3 className="mb-0">{value}{unit}</h3>
      </div>
    </div>
  );
};

export default StatCard;
