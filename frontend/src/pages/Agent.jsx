import React, { useState } from 'react';
import { useUser } from '../context/UserContext';
import RequireUser from '../components/RequireUser';
import { getInsights, adaptPlan } from '../services/api';
import MetricCard from '../components/MetricCard';
import toast from 'react-hot-toast';
import { BookOpen, CheckCircle2, TrendingUp, CalendarDays, Zap } from 'lucide-react';

export default function Agent() {
  const { userId } = useUser();
  const [insights, setInsights] = useState(null);
  const [adapting, setAdapting] = useState(false);
  const [adaptResult, setAdaptResult] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);

  const handleAnalyse = async () => {
    setAnalyzing(true);
    try {
      const { data } = await getInsights(userId);
      setInsights(data);
    } catch {
      toast.error('Error fetching insights');
    } finally {
      setAnalyzing(false);
    }
  };

  const handleAdapt = async () => {
    setAdapting(true);
    try {
      const { data } = await adaptPlan(userId);
      setAdaptResult(data);
      toast.success('Plan adapted successfully!');
    } catch {
      toast.error('Error during adaptation');
    } finally {
      setAdapting(false);
    }
  };

  return (
    <RequireUser>
      <div className="page">
        <h1 className="page-title">🤖 AI Adaptive Agent</h1>

        <div className="card card-info">
          The adaptive agent analyses your progress, detects weak areas, and automatically adjusts
          your study schedule using the <strong>Observe → Plan → Act → Reflect</strong> loop.
        </div>

        {/* Insights */}
        <div className="card" style={{ marginTop: '1.5rem' }}>
          <h3>📡 Current Insights</h3>
          <button className="btn btn-primary" onClick={handleAnalyse} disabled={analyzing}>
            {analyzing ? '🔍 Analysing…' : '🔍 Analyse My Progress'}
          </button>

          {insights && (
            <div style={{ marginTop: '1rem' }}>
              <div className="metrics-row">
                <MetricCard
                  label="Topics"
                  value={insights.observation.total_topics}
                  icon={<BookOpen size={20} />}
                />
                <MetricCard
                  label="Completed"
                  value={insights.observation.completed_topics}
                  icon={<CheckCircle2 size={20} />}
                  color="#10b981"
                />
                <MetricCard
                  label="Progress"
                  value={`${insights.observation.overall_progress_pct}%`}
                  icon={<TrendingUp size={20} />}
                  color="#f59e0b"
                />
              </div>

              {insights.observation.days_until_next_exam != null && (
                <div className="card card-info" style={{ marginTop: '1rem' }}>
                  <CalendarDays size={16} /> Next exam in{' '}
                  <strong>{insights.observation.days_until_next_exam}</strong> days
                </div>
              )}

              {insights.observation.weak_topics?.length > 0 && (
                <div className="card card-warning" style={{ marginTop: '1rem' }}>
                  <h4>⚠️ Weak Topics</h4>
                  <ul>
                    {insights.observation.weak_topics.map((t, i) => (
                      <li key={i}>
                        <strong>{t.name}</strong> — {t.completion}% complete
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="card" style={{ marginTop: '1rem' }}>
                <h4>💡 AI Recommendations</h4>
                <ul className="insights-list">
                  {insights.plan.messages.map((msg, i) => (
                    <li key={i}>{msg}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </div>

        {/* Adapt */}
        <div className="card" style={{ marginTop: '1.5rem' }}>
          <h3>🔄 Trigger Adaptive Re-planning</h3>
          <p className="text-muted">
            This will re-generate your schedule based on current progress.
          </p>
          <button className="btn btn-primary" onClick={handleAdapt} disabled={adapting}>
            <Zap size={14} />
            {adapting ? ' Adapting…' : ' Adapt My Plan'}
          </button>

          {adaptResult && (
            <div style={{ marginTop: '1rem' }}>
              <div className="card card-success">
                <p>
                  <strong>Schedule entries created:</strong>{' '}
                  {adaptResult.schedule_entries_created}
                </p>
                <p>
                  <strong>Topics boosted:</strong> {adaptResult.topics_boosted}
                </p>
              </div>
              <h4 style={{ marginTop: '0.75rem' }}>Agent Insights</h4>
              <ul className="insights-list">
                {adaptResult.insights.map((msg, i) => (
                  <li key={i}>{msg}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </RequireUser>
  );
}
