import React, { useEffect, useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useUser } from '../context/UserContext';
import RequireUser from '../components/RequireUser';
import { createTopic, generateQuiz, listSubjects, listTopics, submitQuiz } from '../services/api';
import toast from 'react-hot-toast';
import { BrainCircuit, Layers3, Sparkles } from 'lucide-react';

export default function Quiz() {
  const { userId } = useUser();
  const location = useLocation();
  const incomingTopicId = location.state?.topicId || '';
  const incomingGeneratedQuiz = location.state?.generatedQuiz || null;
  const [quiz, setQuiz] = useState(null);
  const [answers, setAnswers] = useState({});
  const [result, setResult] = useState(null);
  const [subjects, setSubjects] = useState([]);
  const [availableTopics, setAvailableTopics] = useState([]);
  const [readyQueue, setReadyQueue] = useState(location.state?.readyQuizzes || []);
  const [mode, setMode] = useState('existing');
  const [topicId, setTopicId] = useState('');
  const [subjectId, setSubjectId] = useState('');
  const [customTopicName, setCustomTopicName] = useState('');
  const [activeCustomTopic, setActiveCustomTopic] = useState('');
  const [difficulty, setDifficulty] = useState('medium');
  const [numQ, setNumQ] = useState(5);
  const [generating, setGenerating] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const selectedTopic = availableTopics.find((topic) => topic.id === topicId) || null;
  const selectedSubject = subjects.find((subject) => subject.id === subjectId) || null;
  const fallbackCustomSubject = selectedSubject || subjects[0] || null;

  const loadSubjectsAndTopics = async () => {
    try {
      const { data: subjectRows } = await listSubjects(userId);
      setSubjects(subjectRows);
      if (!subjectId && subjectRows.length > 0) {
        setSubjectId(subjectRows[0].id);
      }
      const topicGroups = await Promise.all(subjectRows.map((subject) => listTopics(subject.id)));
      const flattened = topicGroups.flatMap((group, idx) =>
        group.data.map((topic) => ({
          ...topic,
          subject_name: subjectRows[idx]?.name || 'Subject',
        })),
      );
      setAvailableTopics(flattened);
      if (!topicId && flattened.length > 0) {
        setTopicId(flattened[0].id);
      }
    } catch {
      setSubjects([]);
      setAvailableTopics([]);
    }
  };

  useEffect(() => {
    if (userId) {
      loadSubjectsAndTopics();
    }
  }, [userId]);

  useEffect(() => {
    if (!incomingTopicId) return;
    setTopicId(incomingTopicId);
    setActiveCustomTopic('');
    setMode('existing');
  }, [incomingTopicId]);

  useEffect(() => {
    if (location.state?.readyQuizzes?.length) {
      setReadyQueue(location.state.readyQuizzes);
    }
    if (location.state?.generatedQuiz) {
      setQuiz(location.state.generatedQuiz);
      setResult(null);
      setAnswers({});
    }
  }, [location.state]);

  useEffect(() => {
    if (!incomingGeneratedQuiz) return;
    setQuiz(incomingGeneratedQuiz);
    setResult(null);
    setAnswers({});
  }, [incomingGeneratedQuiz]);

  useEffect(() => {
    if (!quiz && !result && readyQueue.length > 0) {
      const [next, ...rest] = readyQueue;
      setQuiz(next);
      setReadyQueue(rest);
      setAnswers({});
      setResult(null);
    }
  }, [readyQueue, quiz, result]);

  const generatorLabel = useMemo(() => {
    if (mode === 'custom') {
      return customTopicName || activeCustomTopic || 'Enter any topic and generate a quiz for that exact topic';
    }
    if (activeCustomTopic) {
      return activeCustomTopic;
    }
    return selectedTopic ? selectedTopic.name : 'Choose an existing topic';
  }, [mode, customTopicName, activeCustomTopic, selectedTopic]);

  const generateForTopicId = async (
    resolvedTopicId,
    nextDifficulty = difficulty,
    useSubjectContext = true,
  ) => {
    const { data } = await generateQuiz({
      user_id: userId,
      topic_id: resolvedTopicId,
      difficulty: nextDifficulty,
      num_questions: numQ,
      use_subject_context: useSubjectContext,
    });
    setQuiz(data);
    setResult(null);
    setAnswers({});
    setDifficulty(nextDifficulty);
    return data;
  };

  const handleGenerate = async (e) => {
    e.preventDefault();
    setGenerating(true);
    setResult(null);
    setAnswers({});
    try {
      let resolvedTopicId = topicId;
      if (mode === 'custom') {
        const resolvedSubjectId = fallbackCustomSubject?.id || '';
        if (!resolvedSubjectId) {
          toast.error('Create at least one subject first');
          return;
        }
        if (!customTopicName.trim()) {
          toast.error('Enter a topic name');
          return;
        }
        const { data: createdTopic } = await createTopic({
          subject_id: resolvedSubjectId,
          name: customTopicName.trim(),
          difficulty: 0.5,
          estimated_hours: 2,
        });
        resolvedTopicId = createdTopic.id;
        setActiveCustomTopic(createdTopic.name);
        setTopicId(createdTopic.id);
        await loadSubjectsAndTopics();
        setCustomTopicName('');
      } else {
        setActiveCustomTopic('');
      }
      await generateForTopicId(resolvedTopicId, difficulty, mode !== 'custom');
      toast.success('Quiz generated with Groq');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Quiz generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const handleSubmit = async () => {
    const payload = {
      quiz_id: quiz.id,
      user_id: userId,
      include_in_progress: true,
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

  const handleRandomTopic = () => {
    if (!availableTopics.length) {
      toast.error('No topics available for quiz generation');
      return;
    }
    const pool = availableTopics.filter((topic) => topic.id !== topicId);
    const source = pool.length ? pool : availableTopics;
    const selected = source[Math.floor(Math.random() * source.length)];
    setTopicId(selected.id);
    setActiveCustomTopic('');
    setMode('existing');
    toast.success(`Selected: ${selected.name}`);
  };

  const handleTakeAnotherQuiz = async () => {
    if (!topicId) {
      toast.error('Select a topic first');
      return;
    }
    setGenerating(true);
    try {
      await generateForTopicId(topicId, difficulty, !activeCustomTopic);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Quiz generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const handleGenerateNextLevel = async () => {
    if (result?.next_quiz && !activeCustomTopic) {
      handleStartReadyQuiz(result.next_quiz);
      return;
    }
    const nextDifficulty = { easy: 'medium', medium: 'hard', hard: 'hard' }[difficulty] || 'medium';
    setGenerating(true);
    try {
      await generateForTopicId(topicId, nextDifficulty, !activeCustomTopic);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Quiz generation failed');
    } finally {
      setGenerating(false);
    }
  };

  return (
    <RequireUser>
      <div className="page page-wide quiz-page quiz-page-strong">
        <section className="quiz-hero quiz-hero-compact">
          <div className="quiz-hero-copy">
            <span className="quiz-kicker">
              <BrainCircuit size={14} />
              Quiz Lab
            </span>
            <h1 className="quiz-title">Generate quizzes for stored or custom topics.</h1>
            <p className="quiz-subtitle">Pick an existing syllabus topic, or create a new one and let Groq generate the quiz from it immediately.</p>
            <p className="quiz-hero-note">
              {mode === 'custom'
                ? 'Custom topics generate quizzes only for the topic you enter.'
                : 'Choose a saved topic, set difficulty, and start practicing instantly.'}
            </p>
          </div>
        </section>

        {readyQueue.length > 0 && !quiz && !result && (
          <div className="card quiz-ready-card">
            <h3>Ready quizzes</h3>
            <div className="quiz-ready-list">
              {readyQueue.map((rq) => (
                <div key={rq.id} className="quiz-ready-row">
                  <div>
                    <strong>{rq.difficulty.charAt(0).toUpperCase() + rq.difficulty.slice(1)} level</strong> · {rq.total_questions} questions
                  </div>
                  <button className="btn btn-sm btn-outline" onClick={() => handleStartReadyQuiz(rq)} disabled={Boolean(quiz)}>
                    Start quiz
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {!quiz && !result && (
          <form className="card form quiz-generator-card quiz-generator-card-strong" onSubmit={handleGenerate}>
            <div className="quiz-generator-layout">
              <div>
                <div className="quiz-section-head">
                  <h3>Generate quiz</h3>
                  <p>Use a saved topic or create your own topic name for quiz generation.</p>
                </div>

                <div className="quiz-mode-switch">
                  <button
                    type="button"
                    className={`quiz-mode-btn ${mode === 'existing' ? 'active' : ''}`}
                    onClick={() => {
                      setMode('existing');
                      setActiveCustomTopic('');
                    }}
                  >
                    <Layers3 size={15} /> Existing topic
                  </button>
                  <button
                    type="button"
                    className={`quiz-mode-btn ${mode === 'custom' ? 'active' : ''}`}
                    onClick={() => setMode('custom')}
                  >
                    <Sparkles size={15} /> Custom topic
                  </button>
                </div>

                {mode === 'existing' ? (
                  <div className="quiz-generator-grid">
                    <div className="form-group quiz-topic-field">
                      <label>Topic</label>
                      <select value={topicId} onChange={(e) => setTopicId(e.target.value)} required>
                        <option value="" disabled>Select a topic</option>
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
                      <input type="number" min="1" max="20" value={numQ} onChange={(e) => setNumQ(+e.target.value)} />
                    </div>
                  </div>
                ) : (
                  <div className="quiz-generator-grid quiz-generator-grid-custom">
                    <div className="form-group quiz-topic-field">
                      <label>New topic</label>
                      <input
                        value={customTopicName}
                        onChange={(e) => setCustomTopicName(e.target.value)}
                        placeholder="Example: Unit 6: Fourier Series Applications"
                        required
                      />
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
                      <input type="number" min="1" max="20" value={numQ} onChange={(e) => setNumQ(+e.target.value)} />
                    </div>
                  </div>
                )}
              </div>

              <aside className="quiz-side-note">
                <strong>Groq only</strong>
                <p>Each quiz is generated from the selected topic only. No static fillers are used.</p>
                <button className="btn btn-outline" type="button" onClick={handleRandomTopic} disabled={generating || mode === 'custom'}>
                  Pick Random Topic
                </button>
              </aside>
            </div>

            <button className="btn btn-primary" type="submit" disabled={generating}>
              {generating ? 'Generating with Groq...' : 'Generate Quiz'}
            </button>
          </form>
        )}

        {quiz && (
          <div className="card no-select quiz-paper quiz-paper-strong">
            <h3>Quiz - {quiz.difficulty.charAt(0).toUpperCase() + quiz.difficulty.slice(1)} Level</h3>
            <div className="quiz-paper-meta">
              <span>{quiz.questions.length} questions</span>
              <span>{activeCustomTopic || selectedTopic?.name || 'Topic-linked'}</span>
            </div>
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
            <button className="btn btn-primary" onClick={handleSubmit} disabled={submitting || Object.keys(answers).length < quiz.questions.length}>
              {submitting ? 'Submitting...' : 'Submit Answers'}
            </button>
          </div>
        )}

        {result && (
          <div className="card quiz-results-card">
            <h3>Results: {result.correct_count}/{result.total_questions} ({result.score_pct}%)</h3>
            <p>
              Target to pass: <strong>{result.pass_threshold}%</strong> | Status: <strong>{result.passed ? 'Passed' : 'Not cleared'}</strong>
            </p>
            <div className={result.passed ? 'card card-success' : 'card card-warning'}>
              {result.recommendation}
              {!result.passed && result.review_session_created ? ' A revision session was added to your schedule.' : ''}
            </div>
            {result.next_quiz && (
              <div className="card card-info quiz-inline-note">
                Next level ready: <strong>{result.next_quiz.difficulty}</strong> with <strong>{result.next_quiz.total_questions}</strong> questions.
              </div>
            )}
            {readyQueue.length > 0 && (
              <div className="card card-info quiz-inline-note">
                Next level queued ({readyQueue.length} remaining).
              </div>
            )}
            {result.details.map((d, i) => (
              <div key={i} className={`quiz-result-row ${d.is_correct ? 'correct' : 'wrong'}`}>
                <span>{d.is_correct ? '[Correct]' : '[Wrong]'}</span>
                <span>
                  Your answer: <strong>{d.your_answer}</strong> | Correct: <strong>{d.correct_answer}</strong>
                </span>
                <p className="text-muted">{d.explanation}</p>
              </div>
            ))}
            <div className="quiz-results-actions">
              <button className="btn btn-outline" onClick={handleTakeAnotherQuiz} disabled={generating}>
                {generating ? 'Generating...' : 'Take Another Quiz'}
              </button>
              <button className="btn btn-primary" onClick={handleGenerateNextLevel} disabled={generating || !topicId}>
                {result.next_quiz ? 'Start next level' : difficulty === 'hard' ? 'Generate Hard Quiz' : 'Next Level'}
              </button>
            </div>
          </div>
        )}
      </div>
    </RequireUser>
  );
}

