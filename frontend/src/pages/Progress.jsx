import React, { useEffect, useState } from 'react';
import { useUser } from '../context/UserContext';
import RequireUser from '../components/RequireUser';
import MetricCard from '../components/MetricCard';
import { getProgressDashboard, updateProgress } from '../services/api';
import toast from 'react-hot-toast';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts';
import { BookOpen, CheckCircle2, TrendingUp, Clock } from 'lucide-react';

const COLORS = ['#10b981', '#f59e0b', '#4a90d9'];

export default function Progress() {
  const { userId } = useUser();
  const [dashboard, setDashboard] = useState(null);

  const [topicId, setTopicId] = useState('');
  const [comp, setComp] = useState(50);
  const [mins, setMins] = useState(30);
  const [notes, setNotes] = useState('');

  const load = async () => {
    try {
      const { data } = await getProgressDashboard(userId);
      setDashboard(data);
    } catch {}
  };

  useEffect(() => {
    if (userId) load();
  }, [userId]);

  const handleLog = async (e) => {
    e.preventDefault();
    try {
      await updateProgress({
        user_id: userId,
        topic_id: topicId,
        completion_pct: comp,
        time_spent_mins: mins,
        notes: notes || null,
      });
      toast.success('Progress recorded!');
      setTopicId('');
      setNotes('');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Error');
    }
  };

  const pieData = dashboard
    ? [
        { name: 'Completed', value: dashboard.completed_topics },
        {
          name: 'In Progress',
          value: dashboard.total_topics - dashboard.completed_topics,
        },
      ].filter((d) => d.value > 0)
    : [];

  const schedulePct = dashboard?.total_schedule_entries
    ? (dashboard.completed_schedule_entries / dashboard.total_schedule_entries) * 100
    : 0;
  const quizPassPct = dashboard?.total_quizzes_taken
    ? (dashboard.quizzes_passed / dashboard.total_quizzes_taken) * 100
    : 0;

  return (
    <RequireUser>
      <div className="page">
        <h1 className="page-title">Progress &amp; Analytics</h1>

        <form className="card form" onSubmit={handleLog}>
          <h3>Log Study Session</h3>
          <div className="form-row">
            <div className="form-group" style={{ flex: 2 }}>
              <label>Topic ID</label>
              <input value={topicId} onChange={(e) => setTopicId(e.target.value)} required />
            </div>
            <div className="form-group">
              <label>Completion %: {comp}</label>
              <input
                type="range"
                min="0"
                max="100"
                value={comp}
                onChange={(e) => setComp(+e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Time (min)</label>
              <input
                type="number"
                min="0"
                step="5"
                value={mins}
                onChange={(e) => setMins(+e.target.value)}
              />
            </div>
          </div>
          <div className="form-group">
            <label>Notes</label>
            <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} />
          </div>
          <button className="btn btn-primary" type="submit">Log Progress</button>
        </form>

        {dashboard && (
          <>
            <div className="metrics-row">
              <MetricCard
                label="Total Topics"
                value={dashboard.total_topics}
                icon={<BookOpen size={20} />}
              />
              <MetricCard
                label="Completed"
                value={dashboard.completed_topics}
                icon={<CheckCircle2 size={20} />}
                color="#10b981"
              />
              <MetricCard
                label="Progress"
                value={`${dashboard.overall_completion_pct}%`}
                icon={<TrendingUp size={20} />}
                color="#f59e0b"
              />
              <MetricCard
                label="Study Time"
                value={`${Math.round(dashboard.total_time_spent_mins)} min`}
                icon={<Clock size={20} />}
                color="#8b5cf6"
              />
              <MetricCard
                label="Schedule Done"
                value={`${dashboard.completed_schedule_entries}/${dashboard.total_schedule_entries}`}
                icon={<CheckCircle2 size={20} />}
                color="#4a90d9"
              />
            </div>

            <div className="charts-row">
              <div className="card chart-card">
                <h3>Overall Progress</h3>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart
                    data={[
                      { name: 'Overall', value: dashboard.overall_completion_pct },
                      { name: 'Schedule', value: schedulePct },
                      { name: 'Avg Quiz', value: dashboard.average_quiz_score ?? 0 },
                      { name: 'Quiz Pass', value: quizPassPct },
                    ]}
                  >
                    <XAxis dataKey="name" />
                    <YAxis domain={[0, 100]} />
                    <Tooltip />
                    <Bar dataKey="value" fill="#4A90D9" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              <div className="card chart-card">
                <h3>Topic Distribution</h3>
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} label>
                      {pieData.map((_, i) => (
                        <Cell key={i} fill={COLORS[i % COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="card card-info">
              Quiz average: <strong>{dashboard.average_quiz_score ?? 0}%</strong> | Passed quizzes:{' '}
              <strong>{dashboard.quizzes_passed}/{dashboard.total_quizzes_taken}</strong> | Completed schedule sessions:{' '}
              <strong>{dashboard.completed_schedule_entries}/{dashboard.total_schedule_entries}</strong>
            </div>
          </>
        )}
      </div>
    </RequireUser>
  );
}
