import React from 'react';
import {
  PieChart, Pie, Cell, ResponsiveContainer, Legend, Tooltip,
  LineChart, Line, XAxis, YAxis, CartesianGrid
} from 'recharts';

const COLORS = ['#0d6efd', '#6610f2', '#6f42c1', '#d63384', '#dc3545', '#fd7e14', '#ffc107', '#198754'];
const MODE_COLORS = ['#0dcaf0', '#20c997', '#ffc107', '#fd7e14'];

export const ActionPieChart = ({ data }) => {
  const chartData = data.map(item => ({
    name: item.action_type,
    value: item.count
  }));

  return (
    <ResponsiveContainer width="100%" height={250}>
      <PieChart>
        <Pie
          data={chartData}
          cx="50%"
          cy="50%"
          innerRadius={60}
          outerRadius={80}
          paddingAngle={5}
          dataKey="value"
        >
          {chartData.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip />
        <Legend verticalAlign="bottom" height={36}/>
      </PieChart>
    </ResponsiveContainer>
  );
};

export const ModePieChart = ({ data }) => {
  const chartData = data.map(item => ({
    name: item.service_mode,
    value: item.count
  }));

  return (
    <ResponsiveContainer width="100%" height={250}>
      <PieChart>
        <Pie
          data={chartData}
          cx="50%"
          cy="50%"
          outerRadius={80}
          dataKey="value"
          label
        >
          {chartData.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={MODE_COLORS[index % MODE_COLORS.length]} />
          ))}
        </Pie>
        <Tooltip />
        <Legend verticalAlign="bottom" height={36}/>
      </PieChart>
    </ResponsiveContainer>
  );
};

export const KPILineChart = ({ data }) => {
  const chartData = data.map(r => ({
    time: new Date(r.ts_ms).toLocaleTimeString(),
    tpt: r.tpt_mbps,
    bler: r.bler
  }));

  return (
    <ResponsiveContainer width="100%" height={250}>
      <LineChart data={chartData}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="time" hide />
        <YAxis yAxisId="left" orientation="left" stroke="#0d6efd" label={{ value: 'Mbps', angle: -90, position: 'insideLeft' }} />
        <YAxis yAxisId="right" orientation="right" stroke="#dc3545" label={{ value: 'BLER', angle: 90, position: 'insideRight' }} />
        <Tooltip />
        <Legend />
        <Line yAxisId="left" type="monotone" dataKey="tpt" stroke="#0d6efd" dot={false} strokeWidth={2} />
        <Line yAxisId="right" type="monotone" dataKey="bler" stroke="#dc3545" dot={false} strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  );
};
