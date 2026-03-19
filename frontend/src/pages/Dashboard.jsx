import React, { useEffect, useState } from 'react';
import { useUser } from '../context/UserContext';
import { getProgressDashboard } from '../services/api';
import MetricCard from '../components/MetricCard';
import {
  BookOpen,
  CheckCircle2,
  TrendingUp,
  Clock,
  CalendarDays,
  Brain,
  Mic,
  FileQuestion,
  ArrowRight,
  Sparkles,
  Target,
  Layers3,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';

const GUEST_FEATURES = [
  { icon: <CalendarDays size={24} />, text: 'Optimised study schedules' },
  { icon: <TrendingUp size={24} />, text: 'Progress tracking and analytics' },
  { icon: <Brain size={24} />, text: 'AI adaptive replanning' },
  { icon: <FileQuestion size={24} />, text: 'Level-wise quizzes' },
  { icon: <Mic size={24} />, text: 'Voice interaction' },
  { icon: <CheckCircle2 size={24} />, text: 'Smart notifications' },
];

const ACTIONS = [
  {
    icon: <CalendarDays size={18} />,
    title: 'View Schedule',
    text: 'See today\'s sessions and upcoming study blocks.',
    to: '/schedule',
  },
  {
    icon: <FileQuestion size={18} />,
    title: 'Take Quiz',
    text: 'Practice a topic and check recall quickly.',
    to: '/quiz',
  },
  {
    icon: <Brain size={18} />,
    title: 'AI Insights',
    text: 'Review recommendations and planning help.',
    to: '/agent',
  },
  {
    icon: <TrendingUp size={18} />,
    title: 'Progress',
    text: 'Inspect completion trends and study time.',
    to: '/progress',
  },
];

export default function Dashboard() {
  const { userId } = useUser();
  const navigate = useNavigate();
  const [dashboard, setDashboard] = useState(null);
  const completionPct = Number(dashboard?.overall_completion_pct || 0);
  const totalTopics = Number(dashboard?.total_topics || 0);
  const completedTopics = Number(dashboard?.completed_topics || 0);
  const pendingTopics = Math.max(totalTopics - completedTopics, 0);
  const weakTopics = dashboard?.weak_topics || [];

  useEffect(() => {
    if (userId) {
      getProgressDashboard(userId)
        .then((r) => setDashboard(r.data))
        .catch(() => {});
    }
  }, [userId]);

  return (
    <div className="page dashboard-page">
      <section className="dashboard-hero">
        <div className="dashboard-hero-copy">
          <span className="dashboard-kicker">
            <Sparkles size={14} />
            Study Control Center
          </span>
          <h1 className="dashboard-title">
            {userId
              ? 'Your study plan, progress, and next moves in one place.'
              : 'Build a calmer, smarter exam workflow.'}
          </h1>
          <p className="dashboard-subtitle">
            {userId
              ? 'Track momentum, spot weak areas quickly, and jump straight into the next action that matters.'
              : 'Generate schedules from your syllabus, measure real progress, and let the assistant keep your prep organised.'}
          </p>
          <div className="dashboard-hero-actions">
            <button className="btn btn-primary" onClick={() => navigate(userId ? '/schedule' : '/profile')}>
              {userId ? 'Open Schedule' : 'Set Up Profile'}
              <ArrowRight size={16} />
            </button>
            <button className="btn btn-outline" onClick={() => navigate(userId ? '/quiz' : '/')}>
              <FileQuestion size={16} />
              {userId ? 'Practice Quiz' : 'Explore Features'}
            </button>
          </div>
        </div>

        <div className="dashboard-hero-panel">
          <div className="dashboard-status-card">
            <div className="dashboard-status-head">
              <span>Today's Snapshot</span>
              <strong>{userId ? `${completionPct}% complete` : 'Ready to start'}</strong>
            </div>
            <div className="dashboard-status-grid">
              <div>
                <span>Total Topics</span>
                <strong>{userId ? totalTopics : 'Plan'}</strong>
              </div>
              <div>
                <span>Completed</span>
                <strong>{userId ? completedTopics : 'Track'}</strong>
              </div>
              <div>
                <span>Pending</span>
                <strong>{userId ? pendingTopics : 'Improve'}</strong>
              </div>
            </div>
            <div className="dashboard-progress-rail" aria-hidden="true">
              <span style={{ width: `${Math.max(8, completionPct)}%` }} />
            </div>
          </div>
        </div>
      </section>

      {!userId ? (
        <section className="dashboard-empty-shell">
          <div className="dashboard-feature-grid">
            {GUEST_FEATURES.map((feature, index) => (
              <div key={index} className="dashboard-feature-card">
                <div className="dashboard-feature-icon">{feature.icon}</div>
                <span>{feature.text}</span>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <>
          {dashboard && (
            <div className="metrics-row dashboard-metrics-row">
              <MetricCard label="Total Topics" value={totalTopics} icon={<BookOpen size={20} />} />
              <MetricCard
                label="Completed"
                value={completedTopics}
                icon={<CheckCircle2 size={20} />}
                color="#10b981"
              />
              <MetricCard
                label="Progress"
                value={`${completionPct}%`}
                icon={<TrendingUp size={20} />}
                color="#f59e0b"
              />
              <MetricCard
                label="Study Time"
                value={`${Math.round(dashboard.total_time_spent_mins)} min`}
                icon={<Clock size={20} />}
                color="#8b5cf6"
              />
            </div>
          )}

          <section className="dashboard-grid">
            <div className="dashboard-panel dashboard-actions-panel">
              <div className="dashboard-panel-head">
                <h3>Quick Actions</h3>
                <p>Jump into the next thing without digging through the menu.</p>
              </div>
              <div className="dashboard-action-grid">
                {ACTIONS.map((action) => (
                  <button
                    key={action.title}
                    className="dashboard-action-card"
                    onClick={() => navigate(action.to)}
                  >
                    {action.icon}
                    <div>
                      <strong>{action.title}</strong>
                      <span>{action.text}</span>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            <div className="dashboard-side-stack">
              <div className="dashboard-panel dashboard-summary-panel">
                <div className="dashboard-panel-head">
                  <h3>Focus Summary</h3>
                  <p>A quick read on how your prep is moving.</p>
                </div>
                <div className="dashboard-summary-list">
                  <div className="dashboard-summary-item">
                    <Target size={18} />
                    <div>
                      <strong>{pendingTopics} topics pending</strong>
                      <span>Keep moving through the remaining syllabus steadily.</span>
                    </div>
                  </div>
                  <div className="dashboard-summary-item">
                    <Layers3 size={18} />
                    <div>
                      <strong>{weakTopics.length} weak topics flagged</strong>
                      <span>Use quizzes and schedule blocks to reinforce them.</span>
                    </div>
                  </div>
                </div>
              </div>

              {weakTopics.length > 0 && (
                <div className="dashboard-panel dashboard-weak-panel">
                  <div className="dashboard-panel-head">
                    <h3>Needs Attention</h3>
                    <p>Topics that may need another pass soon.</p>
                  </div>
                  <div className="dashboard-weak-list">
                    {weakTopics.slice(0, 6).map((topic, index) => (
                      <div key={index} className="dashboard-weak-item">
                        <span>{topic}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
