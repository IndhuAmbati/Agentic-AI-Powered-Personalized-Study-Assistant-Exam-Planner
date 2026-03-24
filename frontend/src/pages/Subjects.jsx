import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUser } from '../context/UserContext';
import RequireUser from '../components/RequireUser';
import {
  listSubjects,
  deleteSubject,
  createTopic,
  listTopics,
  readyTopicForQuizzes,
  generateQuiz,
} from '../services/api';
import toast from 'react-hot-toast';
import { ChevronDown, ChevronRight, Layers3, Plus, Sparkles, Trash2 } from 'lucide-react';

export default function Subjects() {
  const { userId } = useUser();
  const navigate = useNavigate();
  const [subjects, setSubjects] = useState([]);
  const [expanded, setExpanded] = useState({});
  const [topicsBySubject, setTopicsBySubject] = useState({});
  const [readyLoading, setReadyLoading] = useState({});
  const [topicForms, setTopicForms] = useState({});

  const load = useCallback(async () => {
    if (!userId) return;
    try {
      const { data } = await listSubjects(userId);
      setSubjects(data);
    } catch {
      setSubjects([]);
    }
  }, [userId]);

  useEffect(() => {
    load();
  }, [load]);

  const toggleExpand = async (id) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
    if (!topicsBySubject[id]) {
      try {
        const { data } = await listTopics(id);
        setTopicsBySubject((prev) => ({ ...prev, [id]: data }));
      } catch {}
    }
  };

  const handleDeleteSubject = async (id) => {
    if (!confirm('Delete this subject and all its topics?')) return;
    try {
      await deleteSubject(id);
      toast.success('Deleted');
      load();
    } catch {
      toast.error('Error deleting');
    }
  };

  const handleAddTopic = async (subjectId) => {
    const f = topicForms[subjectId];
    if (!f?.name) return;
    try {
      await createTopic({
        subject_id: subjectId,
        name: f.name,
        difficulty: f.difficulty ?? 0.5,
        estimated_hours: f.hours ?? 2,
      });
      toast.success(`Topic "${f.name}" added`);
      setTopicForms((prev) => ({ ...prev, [subjectId]: { name: '', difficulty: 0.5, hours: 2 } }));
      const { data } = await listTopics(subjectId);
      setTopicsBySubject((prev) => ({ ...prev, [subjectId]: data }));
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Error');
    }
  };

  const updateTopicForm = (subjectId, field, value) => {
    setTopicForms((prev) => ({
      ...prev,
      [subjectId]: { ...(prev[subjectId] || {}), [field]: value },
    }));
  };

  const handleReadyTopic = async (topic) => {
    if (!userId) return;
    setReadyLoading((prev) => ({ ...prev, [topic.id]: true }));
    try {
      const { data } = await readyTopicForQuizzes(topic.id, {
        user_id: userId,
        num_questions: 5,
      });
      setTopicsBySubject((prev) => ({
        ...prev,
        [topic.subject_id]: prev[topic.subject_id]?.map((t) =>
          t.id === topic.id ? { ...t, completed: 1, completion_pct: 100 } : t
        ),
      }));
      if (data.quizzes?.length) {
        navigate('/quiz', {
          state: { readyQuizzes: data.quizzes, topicId: topic.id, source: 'subjects' },
        });
        return;
      }
      const generated = await generateQuiz({
        user_id: userId,
        topic_id: topic.id,
        difficulty: 'medium',
        num_questions: 5,
      });
      navigate('/quiz', {
        state: { generatedQuiz: generated.data, topicId: topic.id, source: 'subjects', autoGenerate: false },
      });
    } catch (err) {
      try {
        const generated = await generateQuiz({
          user_id: userId,
          topic_id: topic.id,
          difficulty: 'medium',
          num_questions: 5,
        });
        navigate('/quiz', {
          state: { generatedQuiz: generated.data, topicId: topic.id, source: 'subjects', autoGenerate: false },
        });
      } catch (generateErr) {
        toast.error(generateErr.response?.data?.detail || 'Quiz generation is unavailable right now.');
        navigate('/quiz', {
          state: { topicId: topic.id, source: 'subjects' },
        });
      }
    } finally {
      setReadyLoading((prev) => ({ ...prev, [topic.id]: false }));
    }
  };

  return (
    <RequireUser>
      <div className="page page-wide subjects-page">
        <section className="subjects-hero">
          <div>
            <span className="subjects-kicker">
              <Layers3 size={14} />
              Study Workspace
            </span>
            <h1 className="page-title">Subjects</h1>
            <p className="subjects-copy">Review each subject, expand its topics, and jump into quizzes from the same workspace.</p>
          </div>
          <div className="subjects-hero-stats">
            <article>
              <span>Subjects</span>
              <strong>{subjects.length}</strong>
            </article>
            <article>
              <span>Open</span>
              <strong>{Object.values(expanded).filter(Boolean).length}</strong>
            </article>
            <article>
              <span>Topics Loaded</span>
              <strong>{Object.values(topicsBySubject).flat().length}</strong>
            </article>
          </div>
        </section>

        <div className="subjects-shell">
          <section className="subjects-list-panel subjects-list-panel-wide">
            {subjects.length === 0 ? (
              <div className="card subjects-empty">
                <Sparkles size={18} />
                <div>
                  <strong>No subjects yet</strong>
                  <p>Your subjects will appear here once they are created or imported.</p>
                </div>
              </div>
            ) : (
              <div className="subjects-list-stack">
                {subjects.map((s) => (
                  <div key={s.id} className="card subject-card subject-card-strong" style={{ borderLeftColor: s.color }}>
                    <div className="subject-header" onClick={() => toggleExpand(s.id)}>
                      <div className="subject-header-main">
                        {expanded[s.id] ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                        <div>
                          <span className="subject-name">{s.name}</span>
                          <div className="subject-meta-row">
                            <span className="badge">Priority {s.priority}</span>
                            {s.exam_date && <span className="badge badge-info">Exam {s.exam_date}</span>}
                            <span className="badge">{(topicsBySubject[s.id] || []).length} topics</span>
                          </div>
                        </div>
                      </div>
                      <button
                        className="btn-icon danger"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteSubject(s.id);
                        }}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>

                    {expanded[s.id] && (
                      <div className="subject-body">
                        <div className="subject-body-head">
                          <div>
                            <h4>Topics</h4>
                            <p className="text-muted">Review progress and launch a quiz directly from each topic.</p>
                          </div>
                        </div>
                        <div className="subject-topic-list">
                          {(topicsBySubject[s.id] || []).map((t) => (
                            <div key={t.id} className="topic-row topic-row-strong">
                              <div className="topic-row-main">
                                <span className="topic-name">{t.name}</span>
                                <span className="text-muted topic-meta">
                                  {t.completed ? 'Completed' : 'In progress'} · Difficulty {t.difficulty} · {t.completion_pct}% done
                                </span>
                              </div>
                              <button
                                className="btn btn-sm btn-outline"
                                disabled={t.completed || readyLoading[t.id]}
                                onClick={() => handleReadyTopic(t)}
                              >
                                {readyLoading[t.id] ? 'Generating...' : 'Take Quiz'}
                              </button>
                            </div>
                          ))}
                        </div>

                        <div className="topic-add-row topic-add-row-strong">
                          <input
                            placeholder="Add another topic"
                            value={topicForms[s.id]?.name || ''}
                            onChange={(e) => updateTopicForm(s.id, 'name', e.target.value)}
                          />
                          <input
                            type="number"
                            min="0"
                            max="1"
                            step="0.1"
                            placeholder="Diff"
                            value={topicForms[s.id]?.difficulty ?? 0.5}
                            onChange={(e) => updateTopicForm(s.id, 'difficulty', +e.target.value)}
                          />
                          <input
                            type="number"
                            min="0.5"
                            step="0.5"
                            placeholder="Hours"
                            value={topicForms[s.id]?.hours ?? 2}
                            onChange={(e) => updateTopicForm(s.id, 'hours', +e.target.value)}
                          />
                          <button className="btn btn-sm btn-primary" onClick={() => handleAddTopic(s.id)}>
                            <Plus size={14} /> Add
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </RequireUser>
  );
}
