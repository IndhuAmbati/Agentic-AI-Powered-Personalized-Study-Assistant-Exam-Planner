import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUser } from '../context/UserContext';
import RequireUser from '../components/RequireUser';
import {
  createSubject,
  listSubjects,
  deleteSubject,
  createTopic,
  listTopics,
  importSyllabus,
  readyTopicForQuizzes,
} from '../services/api';
import toast from 'react-hot-toast';
import { Trash2, Plus, ChevronDown, ChevronRight } from 'lucide-react';

export default function Subjects() {
  const { userId } = useUser();
  const navigate = useNavigate();
  const [subjects, setSubjects] = useState([]);
  const [expanded, setExpanded] = useState({});
  const [topicsBySubject, setTopicsBySubject] = useState({});
  const [readyLoading, setReadyLoading] = useState({});

  // Subject form
  const [sName, setSName] = useState('');
  const [sExam, setSExam] = useState('');
  const [sPriority, setSPriority] = useState(3);
  const [sColor, setSColor] = useState('#4A90D9');
  const [syllabusSubjectName, setSyllabusSubjectName] = useState('');
  const [syllabusExamDate, setSyllabusExamDate] = useState('');
  const [syllabusPriority, setSyllabusPriority] = useState(3);
  const [syllabusColor, setSyllabusColor] = useState('#4A90D9');
  const [defaultTopicHours, setDefaultTopicHours] = useState(2);
  const [defaultTopicDifficulty, setDefaultTopicDifficulty] = useState(0.5);
  const [syllabusText, setSyllabusText] = useState('');

  // Topic forms keyed by subject id
  const [topicForms, setTopicForms] = useState({});

  const load = useCallback(async () => {
    if (!userId) return;
    try {
      const { data } = await listSubjects(userId);
      setSubjects(data);
    } catch {}
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

  const handleAddSubject = async (e) => {
    e.preventDefault();
    try {
      await createSubject({
        user_id: userId,
        name: sName,
        exam_date: sExam || null,
        priority: sPriority,
        color: sColor,
      });
      toast.success(`Subject "${sName}" added`);
      setSName('');
      setSExam('');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Error');
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

  const handleImportSyllabus = async (e) => {
    e.preventDefault();
    try {
      const { data } = await importSyllabus({
        user_id: userId,
        subject_name: syllabusSubjectName,
        exam_date: syllabusExamDate || null,
        priority: syllabusPriority,
        color: syllabusColor,
        default_topic_hours: defaultTopicHours,
        default_topic_difficulty: defaultTopicDifficulty,
        syllabus_text: syllabusText,
      });
      toast.success(`Imported ${data.topics_created} topics for ${data.subject_name}`);
      setSyllabusSubjectName('');
      setSyllabusExamDate('');
      setSyllabusText('');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Error importing syllabus');
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
      toast.success(`Quizzes ready for ${topic.name}`);
      setTopicsBySubject((prev) => ({
        ...prev,
        [topic.subject_id]: prev[topic.subject_id]?.map((t) =>
          t.id === topic.id ? { ...t, completed: 1, completion_pct: 100 } : t
        ),
      }));
      navigate('/quiz', {
        state: { readyQuizzes: data.quizzes, topicId: topic.id, source: 'subjects' },
      });
    } catch (err) {
      navigate('/quiz', {
        state: { topicId: topic.id, source: 'subjects' },
      });
      toast('Opening quiz in direct mode...');
    } finally {
      setReadyLoading((prev) => ({ ...prev, [topic.id]: false }));
    }
  };

  return (
    <RequireUser>
      <div className="page">
        <h1 className="page-title">📖 Subjects &amp; Topics</h1>

        {/* Add Subject */}
        <form className="card form" onSubmit={handleAddSubject}>
          <h3>Add Subject</h3>
          <div className="form-row">
            <div className="form-group" style={{ flex: 2 }}>
              <label>Subject name</label>
              <input value={sName} onChange={(e) => setSName(e.target.value)} required />
            </div>
            <div className="form-group">
              <label>Exam date</label>
              <input type="date" value={sExam} onChange={(e) => setSExam(e.target.value)} />
            </div>
            <div className="form-group">
              <label>Priority ({sPriority})</label>
              <input
                type="range"
                min="0"
                max="5"
                step="0.5"
                value={sPriority}
                onChange={(e) => setSPriority(+e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Colour</label>
              <input type="color" value={sColor} onChange={(e) => setSColor(e.target.value)} />
            </div>
          </div>
          <button className="btn btn-primary" type="submit">
            <Plus size={16} /> Add Subject
          </button>
        </form>

        <form className="card form" onSubmit={handleImportSyllabus} style={{ marginTop: '1rem' }}>
          <h3>Import Syllabus (Bulk Topics)</h3>
          <div className="form-row">
            <div className="form-group" style={{ flex: 2 }}>
              <label>Subject name</label>
              <input
                value={syllabusSubjectName}
                onChange={(e) => setSyllabusSubjectName(e.target.value)}
                required
              />
            </div>
            <div className="form-group">
              <label>Exam date</label>
              <input
                type="date"
                value={syllabusExamDate}
                onChange={(e) => setSyllabusExamDate(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Priority ({syllabusPriority})</label>
              <input
                type="range"
                min="0"
                max="5"
                step="0.5"
                value={syllabusPriority}
                onChange={(e) => setSyllabusPriority(+e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Colour</label>
              <input
                type="color"
                value={syllabusColor}
                onChange={(e) => setSyllabusColor(e.target.value)}
              />
            </div>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>Default topic hours</label>
              <input
                type="number"
                min="0.25"
                step="0.25"
                value={defaultTopicHours}
                onChange={(e) => setDefaultTopicHours(+e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Default difficulty (0-1)</label>
              <input
                type="number"
                min="0"
                max="1"
                step="0.1"
                value={defaultTopicDifficulty}
                onChange={(e) => setDefaultTopicDifficulty(+e.target.value)}
              />
            </div>
          </div>
          <div className="form-group">
            <label>Syllabus topics (one topic per line)</label>
            <textarea
              rows={6}
              value={syllabusText}
              onChange={(e) => setSyllabusText(e.target.value)}
              placeholder={'Unit 1: Basics\nUnit 2: Advanced Concepts\nPractice Set'}
              required
            />
          </div>
          <button className="btn btn-primary" type="submit">
            <Plus size={16} /> Import Syllabus
          </button>
        </form>

        {/* Subjects list */}
        {subjects.length === 0 && (
          <p className="text-muted" style={{ marginTop: '1rem' }}>
            No subjects yet — add one above.
          </p>
        )}

        {subjects.map((s) => (
          <div key={s.id} className="card subject-card" style={{ borderLeftColor: s.color }}>
            <div className="subject-header" onClick={() => toggleExpand(s.id)}>
              {expanded[s.id] ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
              <span className="subject-name">{s.name}</span>
              <span className="badge">Priority {s.priority}</span>
              {s.exam_date && <span className="badge badge-info">Exam: {s.exam_date}</span>}
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
                {/* Topic list */}
                {(topicsBySubject[s.id] || []).map((t) => (
                  <div key={t.id} className="topic-row">
                    <span className="topic-name">
                      {t.completed ? '✅' : '📘'} {t.name}
                    </span>
                    <span className="text-muted">
                      Diff: {t.difficulty} · {t.completion_pct}% · {t.time_spent_mins} min
                    </span>
                    <code className="topic-id">{t.id.slice(0, 8)}</code>
                    <button
                      className="btn btn-sm btn-outline"
                      disabled={t.completed || readyLoading[t.id]}
                      onClick={() => handleReadyTopic(t)}
                    >
                      {readyLoading[t.id] ? 'Generating quizzes…' : 'Ready & Quiz'}
                    </button>
                  </div>
                ))}

                {/* Add topic inline */}
                <div className="topic-add-row">
                  <input
                    placeholder="Topic name"
                    value={topicForms[s.id]?.name || ''}
                    onChange={(e) => updateTopicForm(s.id, 'name', e.target.value)}
                  />
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.1"
                    placeholder="Diff"
                    style={{ width: 80 }}
                    value={topicForms[s.id]?.difficulty ?? 0.5}
                    onChange={(e) => updateTopicForm(s.id, 'difficulty', +e.target.value)}
                  />
                  <input
                    type="number"
                    min="0.5"
                    step="0.5"
                    placeholder="Hours"
                    style={{ width: 80 }}
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
    </RequireUser>
  );
}

