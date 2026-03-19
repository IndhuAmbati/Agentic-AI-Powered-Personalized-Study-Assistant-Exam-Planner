import React, { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useUser } from '../context/UserContext';
import RequireUser from '../components/RequireUser';
import { generateQuiz, submitQuiz, getProgressDashboard, listSubjects, listTopics } from '../services/api';
import toast from 'react-hot-toast';
import MetricCard from '../components/MetricCard';
import { BookOpen, CheckCircle2, TrendingUp } from 'lucide-react';

export default function Quiz() {
  const { userId } = useUser();
  const location = useLocation();
  const incomingTopicId = location.state?.topicId || '';
  const [quiz, setQuiz] = useState(null);
  const [answers, setAnswers] = useState({});
  const [result, setResult] = useState(null);
  const [availableTopics, setAvailableTopics] = useState([]);
  const [readyQueue, setReadyQueue] = useState(location.state?.readyQuizzes || []);
  const [progressSnapshot, setProgressSnapshot] = useState(null);

  // Form
  const [topicId, setTopicId] = useState('');
  const [difficulty, setDifficulty] = useState('medium');
  const [numQ, setNumQ] = useState(5);
  const [generating, setGenerating] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const loadProgress = async () => {
    try {
      const { data } = await getProgressDashboard(userId);
      setProgressSnapshot(data);
    } catch {}
  };

  const loadTopics = async () => {
    try {
      const { data: subjects } = await listSubjects(userId);
      const topicGroups = await Promise.all(subjects.map((subject) => listTopics(subject.id)));
      const flattened = topicGroups.flatMap((group, idx) =>
        group.data.map((topic) => ({
          ...topic,
          subject_name: subjects[idx]?.name || 'Subject',
        }))
      );
      setAvailableTopics(flattened);
      if (!topicId && flattened.length > 0) {
        setTopicId(flattened[0].id);
      }
    } catch {
      setAvailableTopics([]);
    }
  };

  useEffect(() => {
    if (userId) {
      loadProgress();
      loadTopics();
    }
  }, [userId]);

  useEffect(() => {
    if (!incomingTopicId) return;
    setTopicId(incomingTopicId);
  }, [incomingTopicId]);

  useEffect(() => {
    if (location.state?.readyQuizzes?.length) {
      setReadyQueue(location.state.readyQuizzes);
    }
    if (location.state?.topicId) {
      setTopicId(location.state.topicId);
    }
  }, [location.state]);

  useEffect(() => {
    if (!quiz && !result && readyQueue.length > 0) {
      const [next, ...rest] = readyQueue;
      setQuiz(next);
      setReadyQueue(rest);
      setAnswers({});
      setResult(null);
    }
  }, [readyQueue, quiz, result]);

  const handleGenerate = async (e) => {
    e.preventDefault();
    setGenerating(true);
    setResult(null);
    setAnswers({});
    try {
      const { data } = await generateQuiz({
        user_id: userId,
        topic_id: topicId,
        difficulty,
        num_questions: numQ,
      });
      setQuiz(data);
      toast.success(`Quiz generated! ${data.questions.length} questions`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Error');
    } finally {
      setGenerating(false);
    }
  };

  const handleSubmit = async () => {
    const payload = {
      quiz_id: quiz.id,
      user_id: userId,
      answers: Object.entries(answers).map(([qid, a]) => ({
        question_id: qid,
        answer: a,
      })),
    };
    setSubmitting(true);
    try {
      const { data } = await submitQuiz(payload);
      setResult(data);
      setQuiz(null);
      toast.success(`Score: ${data.score_pct}%`);
      loadProgress();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Error submitting');
    } finally {
      setSubmitting(false);
    }
  };

  const handleStartReadyQuiz = (selected) => {
    if (!selected) return;
    setQuiz(selected);
    setResult(null);
    setAnswers({});
    setDifficulty(selected.difficulty);
  };

  const handleStartNextLevel = () => {
    if (!result?.next_quiz) return;
    handleStartReadyQuiz(result.next_quiz);
  };

  const handleRandomTopic = () => {
    if (!availableTopics.length) {
      toast.error('No topics available for quiz generation');
      return;
    }
    const pool = availableTopics.filter((topic) => topic.id !== topicId);
    const source = pool.length ? pool : availableTopics;
    const selected = source[Math.floor(Math.random() * source.length)];
    setTopicId(selected.id);
    toast.success(`Selected: ${selected.subject_name} - ${selected.name}`);
  };

  return (
    <RequireUser>
      <div className="page">
        <h1 className="page-title">📝 Quiz &amp; Assessment</h1>

        {readyQueue.length > 0 && !quiz && !result && (
          <div className="card" style={{ marginBottom: '1rem' }}>
            <h3>Fresh quizzes for your completed topic</h3>
            <div>
              {readyQueue.map((rq) => (
                <div
                  key={rq.id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    marginBottom: '0.5rem',
                  }}
                >
                  <div>
                    <strong>
                      {rq.difficulty.charAt(0).toUpperCase() + rq.difficulty.slice(1)} level
                    </strong>{' '}
                    · {rq.total_questions} questions
                  </div>
                  <button
                    className="btn btn-sm btn-outline"
                    onClick={() => handleStartReadyQuiz(rq)}
                    disabled={Boolean(quiz)}
                  >
                    Start quiz
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Generate */}
        {!quiz && !result && (
          <form className="card form" onSubmit={handleGenerate}>
            <h3>Generate a Quiz</h3>
            {location.state?.source === 'schedule' && incomingTopicId && (
              <div className="card card-info" style={{ marginBottom: '0.75rem' }}>
                Quick mode: your schedule topic is already selected. Choose a level and start when ready.
              </div>
            )}
            <div className="form-row">
              <div className="form-group" style={{ flex: 2 }}>
                <label>Topic</label>
                <select value={topicId} onChange={(e) => setTopicId(e.target.value)} required>
                  <option value="" disabled>
                    Select a topic
                  </option>
                  {availableTopics.map((topic) => (
                    <option key={topic.id} value={topic.id}>
                      {topic.subject_name} - {topic.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label>Difficulty</label>
                <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
                  <option value="easy">Easy</option>
                  <option value="medium">Medium</option>
                  <option value="hard">Hard</option>
                </select>
              </div>
              <div className="form-group">
                <label>Questions</label>
                <input
                  type="number"
                  min="1"
                  max="20"
                  value={numQ}
                  onChange={(e) => setNumQ(+e.target.value)}
                />
              </div>
            </div>
            <div className="form-row" style={{ marginTop: '0.25rem' }}>
              <button className="btn btn-outline" type="button" onClick={handleRandomTopic} disabled={generating}>
                Pick Random Topic
              </button>
            </div>
            <button className="btn btn-primary" type="submit" disabled={generating}>
              {generating ? '🎲 Generating…' : '🎲 Generate Quiz'}
            </button>
          </form>
        )}

        {/* Take quiz */}
        {quiz && (
          <div className="card no-select">
            <h3>
              Quiz — {quiz.difficulty.charAt(0).toUpperCase() + quiz.difficulty.slice(1)} Level
            </h3>
            {quiz.questions.map((q, idx) => (
              <div key={q.id} className="quiz-question">
                <p className="quiz-q-text">
                  <strong>Q{idx + 1}.</strong> {q.question_text}
                </p>
                <div className="quiz-options">
                  {['A', 'B', 'C', 'D'].map((opt) => (
                    <label key={opt} className={`quiz-option ${answers[q.id] === opt ? 'selected' : ''}`}>
                      <input
                        type="radio"
                        name={`q-${q.id}`}
                        value={opt}
                        checked={answers[q.id] === opt}
                        onChange={() => setAnswers((prev) => ({ ...prev, [q.id]: opt }))}
                        disabled={submitting}
                      />
                      <span className="opt-letter">{opt}</span>
                      {q[`option_${opt.toLowerCase()}`]}
                    </label>
                  ))}
                </div>
              </div>
            ))}
            <button
              className="btn btn-primary"
              onClick={handleSubmit}
              disabled={submitting || Object.keys(answers).length < quiz.questions.length}
            >
              {submitting ? '⏳ Submitting…' : '✅ Submit Answers'}
            </button>
          </div>
        )}

        {/* Results */}
        {result && (
          <div className="card">
            <h3>
              🎉 Results: {result.correct_count}/{result.total_questions} ({result.score_pct}%)
            </h3>
            <p>
              Target to pass: <strong>{result.pass_threshold}%</strong> | Status:{' '}
              <strong>{result.passed ? 'Passed' : 'Not cleared'}</strong>
            </p>
            <div className={result.passed ? 'card card-success' : 'card card-warning'}>
              {result.recommendation}
              {!result.passed && result.review_session_created ? ' A revision session was added to your schedule.' : ''}
            </div>
            {result.next_quiz && (
              <div className="card card-info" style={{ marginTop: '0.75rem' }}>
                Next level ready: <strong>{result.next_quiz.difficulty}</strong> with{' '}
                <strong>{result.next_quiz.total_questions}</strong> questions.
              </div>
            )}
            {readyQueue.length > 0 && (
              <div className="card card-info" style={{ marginTop: '0.75rem' }}>
                Next level queued ({readyQueue.length} remaining). Finish reviewing the current result, then start it.
              </div>
            )}
            {result.details.map((d, i) => (
              <div key={i} className={`quiz-result-row ${d.is_correct ? 'correct' : 'wrong'}`}>
                <span>{d.is_correct ? '✅' : '❌'}</span>
                <span>
                  Your answer: <strong>{d.your_answer}</strong> | Correct:{' '}
                  <strong>{d.correct_answer}</strong>
                </span>
                <p className="text-muted">{d.explanation}</p>
              </div>
            ))}
            <button className="btn btn-outline" onClick={() => setResult(null)} style={{ marginTop: '1rem' }}>
              Take Another Quiz
            </button>
            {result.next_quiz && (
              <button
                className="btn btn-primary"
                onClick={handleStartNextLevel}
                style={{ marginTop: '0.5rem', marginLeft: '0.5rem' }}
              >
                Start next level
              </button>
            )}
            {readyQueue.length > 0 && (
              <button
                className="btn btn-primary"
                onClick={() => setResult(null)}
                style={{ marginTop: '0.5rem', marginLeft: '0.5rem' }}
                disabled={quiz !== null}
              >
                Start next level
              </button>
            )}
          </div>
        )}

        {progressSnapshot && (
          <div className="card" style={{ marginTop: '1.5rem' }}>
            <h3>Study Snapshot</h3>
            <div className="metrics-row" style={{ marginBottom: 0 }}>
              <MetricCard
                label="Completed Topics"
                value={`${progressSnapshot.completed_topics}/${progressSnapshot.total_topics}`}
                icon={<CheckCircle2 size={18} />}
                color="#10b981"
              />
              <MetricCard
                label="Overall Completion"
                value={`${progressSnapshot.overall_completion_pct}%`}
                icon={<TrendingUp size={18} />}
                color="#f59e0b"
              />
              <MetricCard
                label="Avg Quiz Score"
                value={
                  progressSnapshot.average_quiz_score != null
                    ? `${progressSnapshot.average_quiz_score}%`
                    : '—'
                }
                icon={<BookOpen size={18} />}
                color="#4a90d9"
              />
            </div>
            {/* Weak-topic list removed per request; monitoring now focuses on completion and quiz score */}
          </div>
        )}
      </div>
    </RequireUser>
  );
}
