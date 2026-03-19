import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useUser } from '../context/UserContext';
import { loginUser, registerUser } from '../services/api';
import toast from 'react-hot-toast';

export default function Login() {
  const navigate = useNavigate();
  const { login } = useUser();
  const [tab, setTab] = useState('login');

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const [name, setName] = useState('');
  const [hours, setHours] = useState(4);
  const [pref, setPref] = useState('balanced');
  const [diff, setDiff] = useState('medium');

  const handleLogin = async (e) => {
    e.preventDefault();
    try {
      const { data } = await loginUser({ email, password });
      login(data.user.id, data.user.name);
      toast.success(`Welcome, ${data.user.name}`);
      navigate('/profile');
    } catch (err) {
      if (err.response?.status === 401) {
        toast.error('Sign-in failed: invalid email or password. If you have not created an account yet, use Register first.');
      } else {
        toast.error(err.response?.data?.detail || 'Login failed');
      }
    }
  };

  const handleRegister = async (e) => {
    e.preventDefault();
    try {
      const { data } = await registerUser({
        name,
        email,
        password,
        daily_study_hours: hours,
        learning_preference: pref,
        difficulty_level: diff,
      });
      login(data.user.id, data.user.name);
      toast.success('Account created');
      navigate('/profile');
    } catch (err) {
      if (err.code === 'ECONNABORTED') {
        toast.error('Registration timed out. Please retry and keep backend running.');
      } else {
        toast.error(err.response?.data?.detail || 'Registration failed');
      }
    }
  };

  return (
    <div className="page">
      <h1 className="page-title">Login</h1>
      <div className="tabs">
        <button className={`tab ${tab === 'login' ? 'active' : ''}`} onClick={() => setTab('login')}>
          Sign In
        </button>
        <button className={`tab ${tab === 'register' ? 'active' : ''}`} onClick={() => setTab('register')}>
          Register
        </button>
      </div>

      {tab === 'login' && (
        <form className="card form" onSubmit={handleLogin}>
          <div className="form-group">
            <label>Email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="form-group">
            <label>Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          <button className="btn btn-primary" type="submit">
            Sign In
          </button>
        </form>
      )}

      {tab === 'register' && (
        <form className="card form" onSubmit={handleRegister}>
          <div className="form-group">
            <label>Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="form-group">
            <label>Email</label>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="form-group">
            <label>Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={6} />
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
            Register
          </button>
        </form>
      )}
    </div>
  );
}
