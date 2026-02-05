import React from 'react';

const items = [
  { term: 'CQI', def: 'Channel Quality Indicator — UE-reported index of downlink channel quality used to select MCS.' },
  { term: 'SINR', def: 'Signal-to-Interference-plus-Noise Ratio — measures link quality; higher is better.' },
  { term: 'BLER', def: 'Block Error Rate — fraction of blocks with decoding errors. Lower is better.' },
  { term: 'MCS', def: 'Modulation and Coding Scheme — discrete index controlling throughput vs reliability.' },
  { term: 'MIMO Rank', def: 'Number of spatial streams used in MIMO transmission (e.g., 1, 2, or 4).' },
  { term: 'TTI', def: 'Transmission Time Interval — scheduling unit duration for air-interface transmissions.' },
  { term: 'eMBB', def: 'Enhanced Mobile Broadband — throughput-oriented service (tolerates higher BLER).' },
  { term: 'URLLC', def: 'Ultra-Reliable Low-Latency Communications — stringent latency and reliability (very low BLER).' },
];

export default function Glossary() {
  return (
    <div className="container-fluid">
      <div className="row">
        <div className="col-12">
          <div className="card border-0 shadow-sm mb-3">
            <div className="card-header bg-white border-0 py-3">
              <h5 className="card-title mb-0">Telecommunication Abbreviations</h5>
            </div>
            <div className="card-body">
              <div className="list-group list-group-flush">
                {items.map(({ term, def }) => (
                  <div key={term} className="list-group-item border-0 px-0 d-flex">
                    <strong className="me-3" style={{ minWidth: 90 }}>{term}</strong>
                    <span className="text-muted">{def}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="card border-0 shadow-sm">
            <div className="card-header bg-white border-0 py-3">
              <h6 className="card-title mb-0">Notes</h6>
            </div>
            <div className="card-body text-muted">
              <ul className="mb-0">
                <li>eMBB typically targets higher throughput and can accept higher BLER (e.g., up to 10%).</li>
                <li>URLLC targets very low BLER (e.g., 0.1%) and low latency with conservative MCS decisions.</li>
                <li>MCS selection balances SINR, BLER constraints, and service mode objectives.</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
