import React, { useEffect, useState } from 'react';
import { useUser } from '../context/UserContext';
import RequireUser from '../components/RequireUser';
import { getProgressDashboard } from '../services/api';
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  RadialBarChart,
  RadialBar,
  BarChart,
  Bar,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
} from 'recharts';
import {
  BarChart3,
  Trophy,
  FileQuestion,
  CheckCircle2,
  XCircle,
  BookOpenCheck,
  TrendingDown,
} from 'lucide-react';

const TOPIC_STATUS_COLORS = ['#10b981', '#1d9bf0', '#cbd5e1'];
const QUIZ_STATUS_COLORS = ['#10b981', '#f59e0b'];

export default function Progress() {
  const { userId } = useUser();
  const [dashboard, setDashboard] = useState(null);

  const load = async () => {
    try {
      const { data } = await getProgressDashboard(userId);
      setDashboard(data);
    } catch {}
  };

  useEffect(() => {
    if (userId) load();
  }, [userId]);

  const averageScore = Number(dashboard?.average_quiz_score || 0);
  const testsTaken = Number(dashboard?.total_quizzes_taken || 0);
  const passed = Number(dashboard?.quizzes_passed || 0);
  const failed = Number(dashboard?.quizzes_failed || Math.max(testsTaken - passed, 0));
  const passRate = testsTaken ? Math.round((passed / testsTaken) * 100) : 0;
  const completionPct = Number(dashboard?.overall_completion_pct || 0);
  const totalTopics = Number(dashboard?.total_topics || 0);
  const completedTopics = Number(dashboard?.completed_topics || 0);
  const coveredTopics = dashboard?.topics_covered || [];
  const weakTopics = dashboard?.weak_topics || [];
  const topicStatusData = (dashboard?.topic_status_breakdown || []).filter((item) => Number(item?.value || 0) > 0);
  const quizSplitData = [
    { name: 'Passed', value: passed },
    { name: 'Not passed', value: failed },
  ].filter((item) => item.value > 0);
  const topicAverages = (dashboard?.topic_quiz_averages || []).slice(0, 6).map((item) => ({
    ...item,
    shortName: item.topic_name.length > 22 ? `${item.topic_name.slice(0, 22)}...` : item.topic_name,
  }));
  const scoreChartData = [{ name: 'Score', value: averageScore, fill: '#1d9bf0' }];

  return (
    <RequireUser>
      <div className="page page-wide progress-page progress-page-simple">
        <section className="page-hero progress-hero progress-hero-simple">
          <div>
            <span className="page-kicker">
              <BarChart3 size={14} />
              Progress
            </span>
            <h1 className="page-title">Progress and quiz analytics</h1>
            <p className="page-copy">Track topic coverage, quiz attempts, weak areas, and your current score trend in one place.</p>
          </div>
        </section>

        {dashboard && (
          <>
            <div className="progress-score-grid">
              <article className="card progress-score-card">
                <span><FileQuestion size={16} /> Quizzes attempted</span>
                <strong>{testsTaken}</strong>
                <p>Total submitted quizzes counted in progress.</p>
              </article>
              <article className="card progress-score-card progress-score-card-primary">
                <span><Trophy size={16} /> Quizzes passed</span>
                <strong>{passed}</strong>
                <p>Quizzes cleared successfully at the current threshold.</p>
              </article>
              <article className="card progress-score-card">
                <span><BookOpenCheck size={16} /> Topics finished</span>
                <strong>{completedTopics}/{totalTopics}</strong>
                <p>Topics fully completed in your syllabus.</p>
              </article>
            </div>

            <div className="progress-visual-grid progress-visual-grid-rich">
              <article className="card progress-visual-card">
                <div className="progress-visual-head">
                  <div>
                    <strong>Overall completion</strong>
                    <p>How much of your full topic list has been completed so far.</p>
                  </div>
                  <span className="progress-visual-pill">{completionPct}%</span>
                </div>
                <div className="progress-chart-wrap">
                  <ResponsiveContainer width="100%" height={220}>
                    <RadialBarChart
                      cx="50%"
                      cy="50%"
                      innerRadius="72%"
                      outerRadius="100%"
                      barSize={18}
                      data={[{ name: 'Completion', value: completionPct, fill: '#10b981' }]}
                      startAngle={90}
                      endAngle={-270}
                    >
                      <RadialBar background dataKey="value" cornerRadius={18} />
                    </RadialBarChart>
                  </ResponsiveContainer>
                  <div className="progress-chart-center">
                    <strong>{completionPct}%</strong>
                    <span>Syllabus done</span>
                  </div>
                </div>
              </article>

              <article className="card progress-visual-card">
                <div className="progress-visual-head">
                  <div>
                    <strong>Average quiz score</strong>
                    <p>Your current performance across all tracked quiz levels.</p>
                  </div>
                  <span className="progress-visual-pill">{averageScore}%</span>
                </div>
                <div className="progress-chart-wrap">
                  <ResponsiveContainer width="100%" height={220}>
                    <RadialBarChart
                      cx="50%"
                      cy="50%"
                      innerRadius="72%"
                      outerRadius="100%"
                      barSize={18}
                      data={scoreChartData}
                      startAngle={90}
                      endAngle={-270}
                    >
                      <RadialBar background dataKey="value" cornerRadius={18} />
                    </RadialBarChart>
                  </ResponsiveContainer>
                  <div className="progress-chart-center">
                    <strong>{averageScore}%</strong>
                    <span>Score average</span>
                  </div>
                </div>
              </article>

              <article className="card progress-visual-card">
                <div className="progress-visual-head">
                  <div>
                    <strong>Quiz result split</strong>
                    <p>Passed versus not-passed quizzes based on the active threshold.</p>
                  </div>
                  <span className="progress-visual-pill progress-visual-pill-success">{passRate}%</span>
                </div>
                <div className="progress-chart-wrap">
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie
                        data={quizSplitData}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        innerRadius={54}
                        outerRadius={82}
                        paddingAngle={3}
                      >
                        {quizSplitData.map((entry, index) => (
                          <Cell key={entry.name} fill={QUIZ_STATUS_COLORS[index % QUIZ_STATUS_COLORS.length]} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="progress-chart-center">
                    <strong>{passRate}%</strong>
                    <span>Pass rate</span>
                  </div>
                </div>
                <div className="progress-pass-breakdown">
                  <div className="progress-pass-item progress-pass-item-success">
                    <span className="progress-pass-icon"><CheckCircle2 size={16} /></span>
                    <strong>{passed}</strong>
                    <span>Passed</span>
                  </div>
                  <div className="progress-pass-item progress-pass-item-muted">
                    <span className="progress-pass-icon"><XCircle size={16} /></span>
                    <strong>{failed}</strong>
                    <span>Not passed</span>
                  </div>
                </div>
              </article>

              <article className="card progress-visual-card">
                <div className="progress-visual-head">
                  <div>
                    <strong>Topic status</strong>
                    <p>See how your topics are split between completed, active, and untouched.</p>
                  </div>
                  <span className="progress-visual-pill">{totalTopics}</span>
                </div>
                <div className="progress-chart-wrap">
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie
                        data={topicStatusData}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        innerRadius={54}
                        outerRadius={82}
                        paddingAngle={3}
                      >
                        {topicStatusData.map((entry, index) => (
                          <Cell key={entry.name} fill={TOPIC_STATUS_COLORS[index % TOPIC_STATUS_COLORS.length]} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="progress-chart-center">
                    <strong>{completedTopics}</strong>
                    <span>Completed</span>
                  </div>
                </div>
                <div className="progress-topic-status-list">
                  {topicStatusData.map((item, index) => (
                    <div key={item.name} className="progress-topic-status-item">
                      <span className="progress-topic-status-dot" style={{ backgroundColor: TOPIC_STATUS_COLORS[index % TOPIC_STATUS_COLORS.length] }} />
                      <strong>{item.value}</strong>
                      <span>{item.name}</span>
                    </div>
                  ))}
                </div>
              </article>
            </div>

            <div className="progress-detail-grid">
              <article className="card progress-detail-card">
                <div className="progress-visual-head">
                  <div>
                    <strong>Topic quiz averages</strong>
                    <p>Lowest averages appear first so weak areas are easier to spot.</p>
                  </div>
                  <span className="progress-visual-pill">{topicAverages.length}</span>
                </div>
                {topicAverages.length ? (
                  <div className="progress-bar-chart-wrap">
                    <ResponsiveContainer width="100%" height={280}>
                      <BarChart data={topicAverages} layout="vertical" margin={{ top: 8, right: 12, left: 12, bottom: 8 }}>
                        <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="rgba(148, 163, 184, 0.25)" />
                        <XAxis type="number" domain={[0, 100]} tickLine={false} axisLine={false} />
                        <YAxis type="category" dataKey="shortName" tickLine={false} axisLine={false} width={120} />
                        <Tooltip formatter={(value, name, item) => [`${value}%`, item?.payload?.topic_name || name]} />
                        <Bar dataKey="average_score" radius={[0, 10, 10, 0]} fill="#1d9bf0" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <p className="progress-topic-empty">Topic-wise quiz averages will appear after you complete quizzes.</p>
                )}
              </article>

              <article className="card progress-detail-card">
                <div className="progress-topic-panels">
                  <section className="progress-topic-panel">
                    <div className="progress-topic-panel-head">
                      <strong>Topics covered</strong>
                      <span>{coveredTopics.length}</span>
                    </div>
                    {coveredTopics.length ? (
                      <div className="progress-topic-tags">
                        {coveredTopics.map((topic) => (
                          <span key={topic} className="progress-topic-tag">{topic}</span>
                        ))}
                      </div>
                    ) : (
                      <p className="progress-topic-empty">Completed topics will appear here after you finish them.</p>
                    )}
                  </section>

                  <section className="progress-topic-panel">
                    <div className="progress-topic-panel-head">
                      <strong>Weak areas</strong>
                      <span>{weakTopics.length}</span>
                    </div>
                    {weakTopics.length ? (
                      <div className="progress-topic-tags progress-topic-tags-warning">
                        {weakTopics.map((topic) => (
                          <span key={topic} className="progress-topic-tag progress-topic-tag-warning">
                            <TrendingDown size={14} />
                            {topic}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="progress-topic-empty">Topics with combined quiz average below 75% will appear here.</p>
                    )}
                  </section>

                  <section className="progress-topic-panel">
                    <div className="progress-topic-panel-head">
                      <strong>Upcoming topics</strong>
                      <span>{(dashboard?.upcoming_topics || []).length}</span>
                    </div>
                    {(dashboard?.upcoming_topics || []).length ? (
                      <div className="progress-topic-tags">
                        {dashboard.upcoming_topics.slice(0, 8).map((topic) => (
                          <span key={topic} className="progress-topic-tag">{topic}</span>
                        ))}
                      </div>
                    ) : (
                      <p className="progress-topic-empty">Your remaining topics will show up here while you work through the syllabus.</p>
                    )}
                  </section>
                </div>
              </article>
            </div>
          </>
        )}

        {!dashboard && (
          <div className="card page-empty-state">
            Scores and topic analytics will appear here once you complete a quiz.
          </div>
        )}
      </div>
    </RequireUser>
  );
}
