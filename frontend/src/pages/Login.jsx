import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Eye, EyeOff, LockKeyhole, Mail, User2 } from 'lucide-react';
import { useUser } from '../context/UserContext';
import { loginUser, registerUser } from '../services/api';
import toast from 'react-hot-toast';

export default function Login() {
  const navigate = useNavigate();
  const { login } = useUser();
  const [tab, setTab] = useState('login');

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showLoginPassword, setShowLoginPassword] = useState(false);
  const [showRegisterPassword, setShowRegisterPassword] = useState(false);

  const [name, setName] = useState('');
  const [hours, setHours] = useState(4);
  const [busy, setBusy] = useState(false);

  const handleLogin = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await loginUser({ email, password });
      login(data.user.id, data.user.name);
      toast.success(`Welcome, ${data.user.name}`);
      navigate('/schedule');
    } catch (err) {
      if (err.response?.status === 401) {
        toast.error('Sign-in failed: invalid email or password.');
      } else {
        toast.error(err.response?.data?.detail || 'Login failed');
      }
    } finally {
      setBusy(false);
    }
  };

  const handleRegister = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await registerUser({
        name,
        email,
        password,
        daily_study_hours: hours,
      });
      login(data.user.id, data.user.name);
      toast.success('Account created');
      navigate('/profile');
    } catch (err) {
      if (err.code === 'ECONNABORTED') {
        toast.error('Registration timed out. Please retry while the backend is running.');
      } else {
        toast.error(err.response?.data?.detail || 'Registration failed');
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page auth-page auth-page-simple">
      <section className="auth-shell auth-shell-simple">
        <div className="card auth-card auth-card-simple">
          <div className="auth-card-head auth-card-head-simple">
            <span className="auth-badge">Study Assistant</span>
            <h2>{tab === 'login' ? 'Sign In' : 'Create Account'}</h2>
            <p>{tab === 'login' ? 'Continue to your workspace.' : 'Create your account and start simply.'}</p>
          </div>

          <div className="tabs auth-tabs auth-tabs-simple">
            <button className={`tab ${tab === 'login' ? 'active' : ''}`} onClick={() => setTab('login')} type="button">
              Sign In
            </button>
            <button className={`tab ${tab === 'register' ? 'active' : ''}`} onClick={() => setTab('register')} type="button">
              Register
            </button>
          </div>

          {tab === 'login' && (
            <form className="form auth-form" onSubmit={handleLogin}>
              <div className="form-group">
                <label>Email</label>
                <div className="auth-input-wrap">
                  <Mail size={16} />
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    required
                  />
                </div>
              </div>
              <div className="form-group">
                <label>Password</label>
                <div className="auth-input-wrap auth-input-wrap-password">
                  <LockKeyhole size={16} />
                  <input
                    type={showLoginPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Enter your password"
                    required
                  />
                  <button
                    type="button"
                    className="auth-eye-btn"
                    onClick={() => setShowLoginPassword((prev) => !prev)}
                    aria-label={showLoginPassword ? 'Hide password' : 'Show password'}
                  >
                    {showLoginPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>
              <button className="btn btn-primary auth-submit" type="submit" disabled={busy}>
                {busy ? 'Signing in...' : 'Sign In'}
              </button>
              <p className="auth-switch-copy">
                Don't have an account?{' '}
                <button type="button" className="auth-switch-link" onClick={() => setTab('register')}>
                  Register
                </button>
              </p>
            </form>
          )}

          {tab === 'register' && (
            <form className="form auth-form" onSubmit={handleRegister}>
              <div className="form-group">
                <label>Name</label>
                <div className="auth-input-wrap">
                  <User2 size={16} />
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Your full name"
                    required
                  />
                </div>
              </div>
              <div className="form-group">
                <label>Email</label>
                <div className="auth-input-wrap">
                  <Mail size={16} />
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    required
                  />
                </div>
              </div>
              <div className="form-group">
                <label>Password</label>
                <div className="auth-input-wrap auth-input-wrap-password">
                  <LockKeyhole size={16} />
                  <input
                    type={showRegisterPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="At least 6 characters"
                    required
                    minLength={6}
                  />
                  <button
                    type="button"
                    className="auth-eye-btn"
                    onClick={() => setShowRegisterPassword((prev) => !prev)}
                    aria-label={showRegisterPassword ? 'Hide password' : 'Show password'}
                  >
                    {showRegisterPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>
              <div className="auth-settings">
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
              </div>
              <button className="btn btn-primary auth-submit" type="submit" disabled={busy}>
                {busy ? 'Creating account...' : 'Create Account'}
              </button>
              <p className="auth-switch-copy">
                Already have an account?{' '}
                <button type="button" className="auth-switch-link" onClick={() => setTab('login')}>
                  Sign In
                </button>
              </p>
            </form>
          )}
        </div>
      </section>
    </div>
  );
}
