import React from 'react';

export default function MetricCard({ label, value, icon, color = 'var(--primary)' }) {
  return (
    <div className="metric-card">
      <div className="metric-icon" style={{ backgroundColor: color + '18', color }}>
        {icon}
      </div>
      <div className="metric-body">
        <p className="metric-value">{value}</p>
        <p className="metric-label">{label}</p>
      </div>
    </div>
  );
}
