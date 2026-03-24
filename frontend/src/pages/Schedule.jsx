import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUser } from '../context/UserContext';
import RequireUser from '../components/RequireUser';
import {
  generateSchedule,
  generateQuiz,
  getSchedule,
  completeEntry,
  unreadEntry,
  skipEntry,
  readyTopicForQuizzes,
  generateScheduleFromSyllabusPdf,
} from '../services/api';
import toast from 'react-hot-toast';
import { CalendarDays, RefreshCw } from 'lucide-react';

export default function Schedule() {
  const { userId, logout } = useUser();
  const [entries, setEntries] = useState([]);
  const [entryLoading, setEntryLoading] = useState({});
  const [loading, setLoading] = useState(false);
  const defaultStartDate = '';
  const defaultEndDate = '';
  const navigate = useNavigate();

  const [startDate, setStartDate] = useState(defaultStartDate);
  const [endDate, setEndDate] = useState(defaultEndDate);
  const [startTime, setStartTime] = useState('08:00');
  const [dailyHours, setDailyHours] = useState(4);
  const [session, setSession] = useState(60);
  const [breakMins, setBreakMins] = useState(15);
  const [coverageEndDate, setCoverageEndDate] = useState('');
  // PDF import form
  const [pdfFile, setPdfFile] = useState(null);
  const [pdfStart, setPdfStart] = useState(defaultStartDate);
  const [pdfEnd, setPdfEnd] = useState(defaultEndDate);
  const [pdfDailyHours, setPdfDailyHours] = useState(4);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfStatus, setPdfStatus] = useState('');

  const openGeneratedQuiz = async (topicId, source) => {
    try {
      const { data } = await generateQuiz({
        user_id: userId,
        topic_id: topicId,
        difficulty: 'medium',
        num_questions: 5,
      });
      navigate('/quiz', {
        state: { generatedQuiz: data, topicId, source, autoGenerate: false },
      });
      return true;
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Quiz generation is unavailable right now.');
      return false;
    }
  };

  const loadSchedule = async () => {
    if (!userId) return;
    try {
      const { data } = await getSchedule(userId);
      setEntries(data);
      setCoverageEndDate('');
    } catch (err) {
      if (err.response?.status === 404) {
        logout();
        toast.error('Your saved session is no longer valid. Sign in again.');
      }
    }
  };

  useEffect(() => {
    loadSchedule();
  }, [userId]);

  const handleComplete = async (entry) => {
    setEntryLoading((prev) => ({ ...prev, [entry.id]: true }));
    try {
      await completeEntry(entry.id);
      toast.success('Marked session read');
      await loadSchedule();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Could not mark read');
    } finally {
      setEntryLoading((prev) => ({ ...prev, [entry.id]: false }));
    }
  };

  const handleUnread = async (entry) => {
    setEntryLoading((prev) => ({ ...prev, [entry.id]: true }));
    try {
      await unreadEntry(entry.id);
      toast.success('Marked session unread');
      await loadSchedule();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Could not mark unread');
    } finally {
      setEntryLoading((prev) => ({ ...prev, [entry.id]: false }));
    }
  };

  const handleSkip = async (entry) => {
    setEntryLoading((prev) => ({ ...prev, [entry.id]: true }));
    try {
      await skipEntry(entry.id);
      toast.success('Session skipped and rescheduled');
      loadSchedule();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to skip session');
    } finally {
      setEntryLoading((prev) => ({ ...prev, [entry.id]: false }));
    }
  };

  const handleTakeQuiz = async (entry) => {
    if (!userId || !entry.topic_id) return;
    setEntryLoading((prev) => ({ ...prev, [entry.id]: true }));
    try {
      const { data } = await readyTopicForQuizzes(entry.topic_id, {
        user_id: userId,
        num_questions: 5,
      });
      if (data.quizzes?.length) {
        navigate('/quiz', {
          state: { readyQuizzes: data.quizzes, topicId: entry.topic_id, source: 'schedule' },
        });
      } else {
        const opened = await openGeneratedQuiz(entry.topic_id, 'schedule');
        if (!opened) {
          navigate('/quiz', {
            state: { topicId: entry.topic_id, source: 'schedule' },
          });
        }
      }
    } catch (err) {
      const opened = await openGeneratedQuiz(entry.topic_id, 'schedule');
      if (!opened) {
        navigate('/quiz', {
          state: { topicId: entry.topic_id, source: 'schedule' },
        });
      }
    } finally {
      setEntryLoading((prev) => ({ ...prev, [entry.id]: false }));
    }
  };

  const handleGenerate = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const { data } = await generateSchedule({
        user_id: userId,
        start_date: startDate,
        end_date: endDate,
        daily_start_time: `${startTime}:00`,
        daily_study_hours: dailyHours,
        session_duration_mins: session,
        break_duration_mins: breakMins,
      });
      setEntries(data);
      const generatedLastDate = data.reduce(
        (latest, entry) => (!latest || entry.scheduled_date > latest ? entry.scheduled_date : latest),
        '',
      );
      const extendedBeyondRequested = generatedLastDate && generatedLastDate > endDate;
      setCoverageEndDate(extendedBeyondRequested ? generatedLastDate : '');
      toast.success(
        extendedBeyondRequested
          ? `Generated ${data.length} study sessions through ${generatedLastDate} to cover the full syllabus.`
          : `Generated ${data.length} study sessions!`,
      );
    } catch (err) {
      if (err.response?.status === 404) {
        logout();
        toast.error('Your saved session is no longer valid. Sign in again.');
      } else if (err.response?.status === 400) {
        toast.error(err.response?.data?.detail || 'Add subjects and topics before generating a schedule.');
      } else {
        toast.error(err.response?.data?.detail || 'Error generating schedule');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateFromPdf = async (e) => {
    e.preventDefault();
    if (!pdfFile) {
      toast.error('Upload a syllabus PDF first.');
      return;
    }
    if (!pdfStart || !pdfEnd) {
      toast.error('Choose start and end dates first.');
      return;
    }
    setPdfLoading(true);
    setPdfStatus('Extracting syllabus topics and generating your schedule. This can take a little while for larger PDFs.');
    try {
      const derivedSubjectName = (pdfFile?.name || '').replace(/\.pdf$/i, '').trim();
      const formData = new FormData();
      formData.append('user_id', userId);
      formData.append('file', pdfFile);
      formData.append('start_date', pdfStart);
      formData.append('end_date', pdfEnd);
      if (derivedSubjectName) {
        formData.append('subject_name', derivedSubjectName);
      }
      formData.append('daily_start_time', '08:00:00');
      formData.append('daily_study_hours', pdfDailyHours);
      formData.append('session_duration_mins', 60);
      formData.append('break_duration_mins', 15);
      formData.append('unit_start', 1);
      formData.append('unit_end', 12);
      formData.append('max_topics_per_unit', 120);
      formData.append('max_topics_per_day', 4);
      formData.append('include_revisions', true);
      formData.append('revision_days', 3);
      formData.append('auto_generate_quizzes', false);
      formData.append('no_ai_mode', false);
      formData.append('import_all_subjects', true);
      formData.append('quiz_difficulty', 'medium');
      formData.append('quiz_questions', 5);

      const { data } = await generateScheduleFromSyllabusPdf(formData);
      setEntries(data.schedule_entries || []);
      setCoverageEndDate(data.coverage_end_date || '');
      setPdfStatus('');
      toast.success(
        data.coverage_end_date && data.coverage_end_date > pdfEnd
          ? `Generated ${data.schedule_entries?.length || 0} sessions and extended through ${data.coverage_end_date} to cover all topics.`
          : `Generated ${data.schedule_entries?.length || 0} sessions from PDF`,
      );
    } catch (err) {
      const detail = err.response?.data?.detail || '';
      if (err.code === 'ECONNABORTED') {
        const message = 'PDF schedule generation timed out. Try a smaller PDF or retry once.';
        toast.error(message);
        setPdfStatus(message);
      } else {
        toast.error(detail || 'Could not generate from PDF');
        setPdfStatus(detail || 'Could not generate from PDF');
      }
    } finally {
      setPdfLoading(false);
    }
  };

  const grouped = entries.reduce((acc, entry) => {
    const dateKey = entry.scheduled_date;
    if (!acc[dateKey]) acc[dateKey] = [];
    acc[dateKey].push(entry);
    return acc;
  }, {});
  const sortedEntries = [...entries].sort((a, b) =>
    `${a.scheduled_date}T${a.start_time}`.localeCompare(`${b.scheduled_date}T${b.start_time}`),
  );
  const firstPendingEntryId = sortedEntries.find((entry) => !entry.completed)?.id;
  const isQuizLocked = (entry) => !entry.completed && firstPendingEntryId && entry.id !== firstPendingEntryId;

  return (
    <RequireUser>
      <div className="page page-wide schedule-page">
        <section className="page-hero schedule-page-hero">
          <div>
            <span className="page-kicker">
              <CalendarDays size={14} />
              Schedule
            </span>
            <h1 className="page-title">Study Schedule</h1>
            <p className="page-copy">Generate a clean study plan from your syllabus PDF and keep each day actionable.</p>
          </div>
          <div className="page-hero-stats">
            <article>
              <span>Sessions</span>
              <strong>{entries.length}</strong>
            </article>
            <article>
              <span>Days Planned</span>
              <strong>{Object.keys(grouped).length}</strong>
            </article>
            <article>
              <span>Coverage</span>
              <strong>{coverageEndDate || 'In range'}</strong>
            </article>
          </div>
        </section>

        <form className="card form schedule-form schedule-upload-card" onSubmit={handleGenerateFromPdf}>
          <h3>Generate from Syllabus PDF</h3>
          <p className="text-muted schedule-upload-copy">Upload one PDF and let the planner turn it into dated study sessions.</p>
          <div className="form-row">
            <div className="form-group" style={{ flex: 2 }}>
              <label>PDF file</label>
              <input
                type="file"
                accept="application/pdf"
                onChange={(e) => setPdfFile(e.target.files?.[0] || null)}
                required
              />
            </div>
            <div className="form-group">
              <label>Start date</label>
              <input type="date" value={pdfStart} onChange={(e) => setPdfStart(e.target.value)} />
            </div>
            <div className="form-group">
              <label>End date</label>
              <input type="date" value={pdfEnd} onChange={(e) => setPdfEnd(e.target.value)} />
            </div>
            <div className="form-group">
              <label>Daily study hours</label>
              <input
                type="number"
                min="0.5"
                max="16"
                step="0.5"
                value={pdfDailyHours}
                onChange={(e) => setPdfDailyHours(+e.target.value)}
              />
            </div>
          </div>

          <button className="btn btn-primary" type="submit" disabled={pdfLoading}>
            <RefreshCw size={14} className={pdfLoading ? 'spin' : ''} />
            {pdfLoading ? ' Generating...' : ' Generate from PDF'}
          </button>
          {pdfStatus && (
            <p className="text-muted" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
              {pdfStatus}
            </p>
          )}
        </form>

        {coverageEndDate && (
          <div className="card">
            <p style={{ margin: 0 }}>
              The plan extends through <strong>{coverageEndDate}</strong> so all syllabus topics are covered.
            </p>
          </div>
        )}

        {Object.keys(grouped).length === 0 && (
          <div className="card page-empty-state">
            No schedule yet. Generate one from your syllabus PDF to start filling this planner.
          </div>
        )}

        {Object.entries(grouped).map(([date, items]) => (
          <div key={date} className="card schedule-day-shell">
            <h3>{date}</h3>
            <table className="table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Subject</th>
                <th>Topic</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((entry) => (
                <tr
                  key={entry.id}
                  className={`schedule-row ${entry.completed ? 'schedule-row-completed' : ''}`}
                >
                  <td>
                    {entry.start_time?.slice(0, 5)} - {entry.end_time?.slice(0, 5)}
                  </td>
                  <td>{entry.subject_name}</td>
                  <td>{entry.topic_name}</td>
                  <td>
                    <div className="schedule-actions">
                      <button
                        className={`btn btn-xs ${entry.completed ? 'btn-success' : 'btn-outline'}`}
                        disabled={entryLoading[entry.id]}
                        onClick={() => handleComplete(entry)}
                      >
                        {entryLoading[entry.id] ? 'Working...' : 'Read'}
                      </button>
                      <button
                        className="btn btn-xs btn-outline"
                        disabled={entryLoading[entry.id] || !entry.completed}
                        onClick={() => handleUnread(entry)}
                      >
                        Unread
                      </button>
                      <button
                        className="btn btn-xs btn-outline"
                        disabled={entryLoading[entry.id]}
                        onClick={() => handleSkip(entry)}
                      >
                        Skip
                      </button>
                      <button
                        className="btn btn-xs btn-primary"
                        disabled={entryLoading[entry.id] || isQuizLocked(entry)}
                        onClick={() => handleTakeQuiz(entry)}
                        title={isQuizLocked(entry) ? 'Complete earlier scheduled sessions first.' : undefined}
                      >
                        Take Quiz
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              </tbody>
            </table>
          </div>
        ))}

      </div>
    </RequireUser>
  );
}


