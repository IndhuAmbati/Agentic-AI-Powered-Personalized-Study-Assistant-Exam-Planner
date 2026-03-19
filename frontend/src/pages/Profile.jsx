import React, { useEffect, useState } from 'react';
import RequireUser from '../components/RequireUser';
import { useUser } from '../context/UserContext';
import {
  getUser,
  updateUser,
  getProgressDashboard,
  getSchedule,
  listSubjects,
  previewSyllabusPdf,
  confirmSyllabusAndGenerate,
  resetSyllabusData,
} from '../services/api';
import toast from 'react-hot-toast';
import {
  CalendarDays,
  Clock3,
  FileText,
  GraduationCap,
  LineChart,
  ShieldCheck,
  Target,
  UserRound,
} from 'lucide-react';

export default function Profile() {
  const { userId, logout } = useUser();
  const [profile, setProfile] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [schedule, setSchedule] = useState([]);
  const [subjects, setSubjects] = useState([]);

  const [name, setName] = useState('');
  const [hours, setHours] = useState(4);
  const [pref, setPref] = useState('balanced');
  const [diff, setDiff] = useState('medium');

  const [pdfFile, setPdfFile] = useState(null);
  const [defaultTopicHours] = useState(2);
  const [defaultTopicDifficulty] = useState(0.5);
  const [planStartDate, setPlanStartDate] = useState(new Date().toISOString().slice(0, 10));
  const [planEndDate, setPlanEndDate] = useState(
    new Date(Date.now() + 60 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
  );
  const dailyStartTime = '08:00';
  const sessionMins = 60;
  const breakMins = 15;
  const maxTopicsPerDay = null;
  const noAiMode = true;
  const [previewSubjects, setPreviewSubjects] = useState([]);
  const [previewReady, setPreviewReady] = useState(false);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const isBusy = isPreviewing || isGenerating || isResetting;

  const loadAll = async () => {
    if (!userId) return;
    try {
      const [{ data: userData }, { data: progressData }, { data: scheduleData }, { data: subjectData }] =
        await Promise.all([
          getUser(userId),
          getProgressDashboard(userId),
          getSchedule(userId),
          listSubjects(userId),
        ]);
      setProfile(userData);
      setName(userData.name || '');
      setHours(userData.daily_study_hours ?? 4);
      setPref(userData.learning_preference || 'balanced');
      setDiff(userData.difficulty_level || 'medium');
      setDashboard(progressData);
      setSchedule(scheduleData);
      setSubjects(subjectData);
    } catch (err) {
      if (err.response?.status === 404) {
        logout();
        toast.error('Your saved session is no longer valid. Sign in again.');
        return;
      }
      toast.error(err.response?.data?.detail || 'Could not load profile data');
    }
  };

  useEffect(() => {
    loadAll();
  }, [userId]);

  const handleSaveProfile = async (e) => {
    e.preventDefault();
    try {
      const { data } = await updateUser(userId, {
        name,
        daily_study_hours: hours,
        learning_preference: pref,
        difficulty_level: diff,
      });
      setProfile(data);
      toast.success('Profile updated');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Profile update failed');
    }
  };

  const handlePdfUpload = async (e) => {
    e.preventDefault();
    if (!pdfFile) {
      toast.error('Please choose a PDF file');
      return;
    }
    if (planStartDate > planEndDate) {
      toast.error('Plan start date must be before or equal to end date');
      return;
    }

    const formData = new FormData();
    formData.append('file', pdfFile);

    try {
      setIsPreviewing(true);
      const { data } = await previewSyllabusPdf(formData);
      const editable = (data.subjects || []).map((subject) => ({
        name: subject.name,
        topicsText: (subject.topics || []).join('\n'),
      }));
      setPreviewSubjects(editable);
      setPreviewReady(true);
      toast.success(`Preview ready: ${data.subjects_detected} subjects, ${data.topics_detected} topics`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'PDF preview failed');
    } finally {
      setIsPreviewing(false);
    }
  };

  const handleAutoGenerateFromPdf = async () => {
    if (!userId) return;
    if (!pdfFile) {
      toast.error('Please choose a PDF file');
      return;
    }
    if (planStartDate > planEndDate) {
      toast.error('Plan start date must be before or equal to end date');
      return;
    }

    try {
      setIsGenerating(true);
      let subjects = previewSubjects
        .map((subject) => ({
          name: (subject.name || '').trim(),
          topics: (subject.topicsText || '')
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean),
        }))
        .filter((subject) => subject.name && subject.topics.length > 0);

      if (!subjects.length) {
        const formData = new FormData();
        formData.append('file', pdfFile);
        const { data: previewData } = await previewSyllabusPdf(formData);
        const editable = (previewData.subjects || []).map((subject) => ({
          name: subject.name,
          topicsText: (subject.topics || []).join('\n'),
        }));
        setPreviewSubjects(editable);
        setPreviewReady(true);
        subjects = (previewData.subjects || [])
          .map((subject) => ({
            name: (subject.name || '').trim(),
            topics: (subject.topics || []).map((topic) => String(topic).trim()).filter(Boolean),
          }))
          .filter((subject) => subject.name && subject.topics.length > 0);
      }

      if (subjects.length === 0) {
        toast.error('No valid subjects/topics were extracted from the PDF');
        return;
      }

      const payload = {
        user_id: userId,
        start_date: planStartDate,
        end_date: planEndDate,
        daily_start_time: dailyStartTime + ':00',
        daily_study_hours: hours,
        session_duration_mins: sessionMins,
        break_duration_mins: breakMins,
        max_topics_per_day: maxTopicsPerDay,
        default_topic_hours: defaultTopicHours,
        default_topic_difficulty: defaultTopicDifficulty,
        no_ai_mode: noAiMode,
        clear_existing: true,
        subjects,
      };

      const { data: generatedData } = await confirmSyllabusAndGenerate(payload);
      setPreviewReady(false);
      setPreviewSubjects([]);
      await loadAll();
      toast.success(
        `Extracted ${generatedData.topics_created} topics and generated ${generatedData.schedule_entries.length} sessions`
      );
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Auto-generate from PDF failed');
    } finally {
      setIsGenerating(false);
    }
  };

  const handleResetData = async () => {
    if (!userId) return;
    if (!confirm('Clear all subjects, topics, schedule, progress and quizzes for this user?')) return;
    try {
      setIsResetting(true);
      await resetSyllabusData(userId);
      setPreviewReady(false);
      setPreviewSubjects([]);
      await loadAll();
      toast.success('Study data cleared');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Could not clear data');
    } finally {
      setIsResetting(false);
    }
  };

  const updatePreviewSubject = (idx, field, value) => {
    setPreviewSubjects((prev) =>
      prev.map((item, i) => (i === idx ? { ...item, [field]: value } : item))
    );
  };

  const handleConfirmAndGenerate = async () => {
    if (!userId) return;
    const subjects = previewSubjects
      .map((subject) => ({
        name: (subject.name || '').trim(),
        topics: (subject.topicsText || '')
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean),
      }))
      .filter((subject) => subject.name && subject.topics.length > 0);

    if (subjects.length === 0) {
      toast.error('No valid subjects/topics to generate');
      return;
    }

    const payload = {
      user_id: userId,
      start_date: planStartDate,
      end_date: planEndDate,
      daily_start_time: dailyStartTime + ':00',
      daily_study_hours: hours,
      session_duration_mins: sessionMins,
      break_duration_mins: breakMins,
      max_topics_per_day: maxTopicsPerDay,
      default_topic_hours: defaultTopicHours,
      default_topic_difficulty: defaultTopicDifficulty,
      no_ai_mode: noAiMode,
      clear_existing: true,
      subjects,
    };

    try {
      setIsGenerating(true);
      const { data } = await confirmSyllabusAndGenerate(payload);
      toast.success(
        `Created ${data.topics_created} topics and ${data.schedule_entries.length} sessions`
      );
      setPreviewReady(false);
      setPreviewSubjects([]);
      await loadAll();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Generate failed');
    } finally {
      setIsGenerating(false);
    }
  };

  const upcomingSchedule = [...schedule]
    .filter((item) => !item.completed)
    .sort((a, b) =>
      `${a.scheduled_date}T${a.start_time}`.localeCompare(`${b.scheduled_date}T${b.start_time}`)
    );
  const groupedUpcoming = upcomingSchedule.reduce((acc, item) => {
    const dateKey = item.scheduled_date;
    if (!acc[dateKey]) acc[dateKey] = [];
    acc[dateKey].push(item);
    return acc;
  }, {});
  const allSubjectNames = subjects.map((subject) => subject.name).filter(Boolean);
  const scheduledSubjectNames = new Set(
    upcomingSchedule.map((item) => item.subject_name).filter(Boolean)
  );
  const coveredSubjectNames = allSubjectNames.filter((name) => scheduledSubjectNames.has(name));
  const missingSubjectNames = allSubjectNames.filter((name) => !scheduledSubjectNames.has(name));
  const hasFullCoverage = allSubjectNames.length > 0 && missingSubjectNames.length === 0;
  const nextSession = upcomingSchedule[0] || null;
  const weakTopicsCount = dashboard?.weak_topics?.length || 0;

  return (
    <RequireUser>
      <div className="page profile-page">
        <section className="profile-hero">
          <div className="profile-hero-copy">
            <span className="profile-kicker">
              <UserRound size={14} />
              Personal Hub
            </span>
            <h1 className="profile-title">{name || profile?.name || 'Study Profile'}</h1>
            <p className="profile-subtitle">
              Keep your study settings, syllabus import flow, and timetable overview together in one clean workspace.
            </p>
            {profile && (
              <div className="profile-email-row">
                Logged in as <strong>{profile.email}</strong>
              </div>
            )}
          </div>

          <div className="profile-hero-panel">
            <div className="profile-stats-grid">
              <div className="profile-stat-card">
                <Clock3 size={18} />
                <span>Daily Hours</span>
                <strong>{hours}</strong>
              </div>
              <div className="profile-stat-card">
                <GraduationCap size={18} />
                <span>Subjects</span>
                <strong>{subjects.length}</strong>
              </div>
              <div className="profile-stat-card">
                <LineChart size={18} />
                <span>Completion</span>
                <strong>{dashboard?.overall_completion_pct ?? 0}%</strong>
              </div>
              <div className="profile-stat-card">
                <Target size={18} />
                <span>Weak Topics</span>
                <strong>{weakTopicsCount}</strong>
              </div>
            </div>
            {nextSession && (
              <div className="profile-next-session">
                <div className="profile-next-session-head">Next Session</div>
                <strong>{nextSession.subject_name}</strong>
                <span>{nextSession.topic_name}</span>
                <small>
                  {nextSession.scheduled_date} · {nextSession.start_time?.slice(0, 5)} - {nextSession.end_time?.slice(0, 5)}
                </small>
              </div>
            )}
          </div>
        </section>

        {dashboard && (
          <section className="profile-summary-grid profile-summary-grid-strong">
            <div className="summary-tile">
              <span className="summary-label">Topics Completed</span>
              <strong>{dashboard.completed_topics}/{dashboard.total_topics}</strong>
            </div>
            <div className="summary-tile">
              <span className="summary-label">Overall Progress</span>
              <strong>{dashboard.overall_completion_pct}%</strong>
            </div>
            <div className="summary-tile">
              <span className="summary-label">Average Quiz</span>
              <strong>{dashboard.average_quiz_score ?? 'N/A'}%</strong>
            </div>
            <div className="summary-tile">
              <span className="summary-label">Subjects Covered</span>
              <strong>{coveredSubjectNames.length}/{allSubjectNames.length}</strong>
            </div>
          </section>
        )}

        <div className="profile-sections profile-sections-wide">
          <form className="card form profile-card profile-card-strong" onSubmit={handleSaveProfile}>
            <div className="section-head">
              <h3>Profile Settings</h3>
              <p className="text-muted">Tune your learning style, pace, and preferred difficulty.</p>
            </div>
            <div className="form-group">
              <label>Name</label>
              <input value={name} onChange={(e) => setName(e.target.value)} required />
            </div>
            <div className="form-group">
              <label>Daily study hours</label>
              <input
                type="number"
                min="0.5"
                max="16"
                step="0.5"
                value={hours}
                onChange={(e) => setHours(+e.target.value)}
              />
            </div>
            <div className="form-row">
              <div className="form-group">
                <label>Learning preference</label>
                <select value={pref} onChange={(e) => setPref(e.target.value)}>
                  <option value="balanced">Balanced</option>
                  <option value="visual">Visual</option>
                  <option value="reading">Reading</option>
                  <option value="practice">Practice</option>
                </select>
              </div>
              <div className="form-group">
                <label>Difficulty level</label>
                <select value={diff} onChange={(e) => setDiff(e.target.value)}>
                  <option value="easy">Easy</option>
                  <option value="medium">Medium</option>
                  <option value="hard">Hard</option>
                </select>
              </div>
            </div>
            <button className="btn btn-primary" type="submit">
              Save Profile
            </button>
          </form>

          <form className="card form profile-card profile-card-strong" onSubmit={handlePdfUpload}>
            <div className="section-head">
              <h3>Syllabus PDF Workflow</h3>
              <p className="text-muted">Upload once, preview extracted topics, then generate a full timetable.</p>
            </div>
            <div className="profile-upload-tips">
              <div className="profile-chip">
                <FileText size={16} />
                <div>
                  <strong>Preview First</strong>
                  <span>Check extracted subjects and topic lines before generation.</span>
                </div>
              </div>
              <div className="profile-chip">
                <CalendarDays size={16} />
                <div>
                  <strong>Plan Window</strong>
                  <span>Set the start and end dates for your timetable.</span>
                </div>
              </div>
              <div className="profile-chip">
                <ShieldCheck size={16} />
                <div>
                  <strong>Reset Available</strong>
                  <span>Clear old study data before building a fresh plan.</span>
                </div>
              </div>
            </div>
            <div className="form-row">
              <div className="form-group profile-file-field">
                <label>Syllabus PDF</label>
                <input
                  type="file"
                  accept=".pdf,application/pdf"
                  onChange={(e) => setPdfFile(e.target.files?.[0] || null)}
                  required
                />
              </div>
              <div className="form-group">
                <label>Hours per day</label>
                <input
                  type="number"
                  min="0.5"
                  max="16"
                  step="0.5"
                  value={hours}
                  onChange={(e) => setHours(+e.target.value)}
                />
              </div>
            </div>
            <div className="form-row">
              <div className="form-group">
                <label>Plan start date</label>
                <input type="date" value={planStartDate} onChange={(e) => setPlanStartDate(e.target.value)} />
              </div>
              <div className="form-group">
                <label>Plan end date</label>
                <input type="date" value={planEndDate} onChange={(e) => setPlanEndDate(e.target.value)} />
              </div>
            </div>
            <div className="form-row profile-actions">
              <button className="btn btn-primary" type="submit" disabled={isBusy}>
                {isPreviewing ? 'Preparing preview...' : 'Preview Extracted Topics'}
              </button>
              <button
                className="btn btn-primary"
                type="button"
                onClick={handleAutoGenerateFromPdf}
                disabled={isBusy}
              >
                {isGenerating ? 'Generating timetable...' : 'Extract and Generate Timetable'}
              </button>
              <button className="btn btn-outline" type="button" onClick={handleResetData} disabled={isBusy}>
                {isResetting ? 'Clearing data...' : 'Clear Existing Study Data'}
              </button>
            </div>
          </form>
        </div>

        {previewReady && (
          <div className="card form profile-card profile-card-strong">
            <div className="section-head">
              <h3>Review and Edit Topics</h3>
              <p className="text-muted">Edit any subject or topic before final timetable generation.</p>
            </div>
            <div className="preview-grid">
              {previewSubjects.map((subject, idx) => (
                <div key={idx} className="preview-subject-card">
                  <div className="form-group">
                    <label>Subject Name</label>
                    <input
                      value={subject.name}
                      onChange={(e) => updatePreviewSubject(idx, 'name', e.target.value)}
                    />
                  </div>
                  <div className="form-group">
                    <label>Topics (one per line)</label>
                    <textarea
                      rows={8}
                      value={subject.topicsText}
                      onChange={(e) => updatePreviewSubject(idx, 'topicsText', e.target.value)}
                    />
                  </div>
                </div>
              ))}
            </div>
            <div className="form-row profile-actions">
              <button className="btn btn-primary" type="button" onClick={handleConfirmAndGenerate} disabled={isBusy}>
                {isGenerating ? 'Generating...' : 'Confirm and Generate Timetable'}
              </button>
              <button className="btn btn-outline" type="button" onClick={() => setPreviewReady(false)} disabled={isBusy}>
                Cancel Preview
              </button>
            </div>
          </div>
        )}

        <div className="card profile-card profile-card-strong">
          <div className="section-head">
            <h3>Upcoming Timetable ({upcomingSchedule.length})</h3>
            <p className="text-muted">A quick view of what is scheduled next.</p>
          </div>
          {allSubjectNames.length > 0 && (
            <div className={`coverage-banner ${hasFullCoverage ? 'coverage-banner-success' : 'coverage-banner-warning'}`}>
              <strong>
                {hasFullCoverage
                  ? `All subjects are covered in the timetable (${coveredSubjectNames.length}/${allSubjectNames.length}).`
                  : `Subjects covered: ${coveredSubjectNames.length}/${allSubjectNames.length}.`}
              </strong>
              {!hasFullCoverage && missingSubjectNames.length > 0 && (
                <span> Missing: {missingSubjectNames.join(', ')}</span>
              )}
            </div>
          )}
          {upcomingSchedule.length === 0 ? (
            <p className="text-muted">No upcoming sessions.</p>
          ) : (
            Object.entries(groupedUpcoming).map(([date, items]) => (
              <div key={date} className="schedule-day-card">
                <h4>{date}</h4>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Subject</th>
                      <th>Topic</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={item.id}>
                        <td>{item.start_time?.slice(0, 5)} - {item.end_time?.slice(0, 5)}</td>
                        <td>{item.subject_name}</td>
                        <td>{item.topic_name}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))
          )}
        </div>
      </div>
    </RequireUser>
  );
}
