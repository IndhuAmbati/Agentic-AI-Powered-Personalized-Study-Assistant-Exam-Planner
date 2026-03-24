import React, { useEffect, useState } from 'react';
import RequireUser from '../components/RequireUser';
import { useUser } from '../context/UserContext';
import { getUser, updateUser, getProgressDashboard } from '../services/api';
import toast from 'react-hot-toast';
import { Clock3, LineChart, Mail, SlidersHorizontal, Target, Trophy, UserRound } from 'lucide-react';

export default function SimpleProfile() {
  const { userId, logout } = useUser();
  const [profile, setProfile] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [name, setName] = useState('');
  const [hours, setHours] = useState(4);
  const [pref, setPref] = useState('balanced');
  const [diff, setDiff] = useState('medium');

  useEffect(() => {
    const loadProfile = async () => {
      if (!userId) return;
      try {
        const [{ data: userData }, { data: progressData }] = await Promise.all([
          getUser(userId),
          getProgressDashboard(userId),
        ]);
        setProfile(userData);
        setDashboard(progressData);
        setName(userData.name || '');
        setHours(userData.daily_study_hours ?? 4);
        setPref(userData.learning_preference || 'balanced');
        setDiff(userData.difficulty_level || 'medium');
      } catch (err) {
        if (err.response?.status === 404) {
          logout();
          toast.error('Your saved session is no longer valid. Sign in again.');
          return;
        }
        toast.error(err.response?.data?.detail || 'Could not load profile');
      }
    };

    loadProfile();
  }, [userId, logout]);

  const handleSave = async (e) => {
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

  return (
    <RequireUser>
      <div className="page page-wide profile-page profile-page-compact">
        <section className="profile-hero profile-hero-compact">
          <div className="profile-hero-copy">
            <span className="profile-kicker">
              <UserRound size={14} />
              Profile
            </span>
            <h1 className="profile-title">{profile?.name || 'My Profile'}</h1>
            <p className="profile-subtitle">Manage your study style, daily rhythm, and progress snapshot from one clean space.</p>
            {profile?.email && (
              <div className="profile-email-row">
                Logged in as <strong>{profile.email}</strong>
              </div>
            )}
          </div>
          <div className="profile-hero-panel">
            <div className="profile-stats-grid profile-stats-grid-compact">
              <div className="profile-stat-card">
                <Clock3 size={18} />
                <span>Daily Hours</span>
                <strong>{hours}</strong>
              </div>
              <div className="profile-stat-card">
                <SlidersHorizontal size={18} />
                <span>Learning Style</span>
                <strong>{pref}</strong>
              </div>
              <div className="profile-stat-card">
                <Target size={18} />
                <span>Difficulty</span>
                <strong>{diff}</strong>
              </div>
              <div className="profile-stat-card">
                <LineChart size={18} />
                <span>Progress</span>
                <strong>{dashboard?.overall_completion_pct ?? 0}%</strong>
              </div>
            </div>
          </div>
        </section>

        <div className="profile-sections profile-sections-compact">
          <div className="profile-main-stack">
            <div className="card profile-card profile-card-strong profile-account-card">
              <div className="section-head">
                <h3>Account overview</h3>
              </div>
              <div className="profile-account-grid">
                <div className="profile-account-tile">
                  <span className="profile-account-icon"><UserRound size={16} /></span>
                  <div>
                    <small>Name</small>
                    <strong>{profile?.name || name || 'Student'}</strong>
                  </div>
                </div>
                <div className="profile-account-tile">
                  <span className="profile-account-icon"><Mail size={16} /></span>
                  <div>
                    <small>Email</small>
                    <strong>{profile?.email || 'Not available'}</strong>
                  </div>
                </div>
                <div className="profile-account-tile">
                  <span className="profile-account-icon"><Clock3 size={16} /></span>
                  <div>
                    <small>Daily study hours</small>
                    <strong>{hours} hrs</strong>
                  </div>
                </div>
                <div className="profile-account-tile">
                  <span className="profile-account-icon"><Trophy size={16} /></span>
                  <div>
                    <small>Quiz performance</small>
                    <strong>{dashboard?.average_quiz_score ?? 'N/A'}%</strong>
                  </div>
                </div>
              </div>
            </div>

            <form className="card form profile-card profile-card-strong" onSubmit={handleSave}>
              <div className="section-head">
                <h3>Study settings</h3>
              </div>
              <div className="form-group">
                <label>Name</label>
                <input value={name} onChange={(e) => setName(e.target.value)} required />
              </div>
              <div className="form-group">
                <label>Email</label>
                <input value={profile?.email || ''} disabled />
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
              <div className="profile-action-row">
                <button className="btn btn-primary" type="submit">Save Changes</button>
                <button className="btn btn-outline" type="button" onClick={logout}>Logout</button>
              </div>
            </form>
          </div>

          <div className="card profile-card profile-card-strong profile-progress-card-compact">
            <div className="section-head">
              <h3>Progress</h3>
            </div>
            <div className="profile-summary-grid profile-summary-grid-compact">
              <div className="summary-tile">
                <span className="summary-label">Completed Topics</span>
                <strong>{dashboard?.completed_topics || 0}/{dashboard?.total_topics || 0}</strong>
              </div>
              <div className="summary-tile">
                <span className="summary-label">Overall Progress</span>
                <strong>{dashboard?.overall_completion_pct || 0}%</strong>
              </div>
              <div className="summary-tile">
                <span className="summary-label">Avg Quiz</span>
                <strong>{dashboard?.average_quiz_score ?? 'N/A'}%</strong>
              </div>
            </div>
            <div className="profile-progress-bars">
              <div className="profile-progress-bar-card">
                <div className="profile-progress-bar-head">
                  <span>Overall progress</span>
                  <strong>{dashboard?.overall_completion_pct || 0}%</strong>
                </div>
                <div className="profile-progress-track">
                  <span style={{ width: `${Math.max(Number(dashboard?.overall_completion_pct || 0), 6)}%` }} />
                </div>
              </div>
              <div className="profile-progress-bar-card">
                <div className="profile-progress-bar-head">
                  <span>Average quiz score</span>
                  <strong>{dashboard?.average_quiz_score ?? 'N/A'}%</strong>
                </div>
                <div className="profile-progress-track quiz">
                  <span style={{ width: `${Math.max(Number(dashboard?.average_quiz_score || 0), 6)}%` }} />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </RequireUser>
  );
}
