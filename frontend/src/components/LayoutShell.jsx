import React from 'react';
import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { useUser } from '../context/UserContext';
import { useTheme } from '../context/ThemeContext';
import {
  User,
  BookOpen,
  CalendarDays,
  BarChart3,
  Share2,
  FileQuestion,
  MessageSquare,
  LogOut,
  Sparkles,
  Moon,
  Sun,
} from 'lucide-react';

const NAV = [
  { to: '/', label: 'Home', icon: Sparkles },
  { to: '/schedule', label: 'Schedule', icon: CalendarDays },
  { to: '/progress', label: 'Progress', icon: BarChart3 },
  { to: '/mindmap', label: 'Mind Map', icon: Share2 },
  { to: '/quiz', label: 'Quiz', icon: FileQuestion },
  { to: '/chatbot', label: 'Chatbot', icon: MessageSquare },
  { to: '/profile', label: 'Profile', icon: User },
];

export default function LayoutShell() {
  const { userId, userName, logout } = useUser();
  const navigate = useNavigate();
  const { theme, toggleTheme } = useTheme();

  return (
    <div className="app-layout topnav-layout">
      <div className="app-ambient app-ambient-one" />
      <div className="app-ambient app-ambient-two" />

      <header className="topbar">
        <div className="brand" onClick={() => navigate('/')}>
          <span className="logo-badge">
            <BookOpen size={18} />
          </span>
          <div className="brand-copy">
            <strong>Study Assistant</strong>
            <small>Plan, quiz, and track with one workspace</small>
          </div>
        </div>

        <nav className="topnav-links">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => `topnav-link ${isActive ? 'active' : ''}`}
            >
              <Icon size={15} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="topnav-user">
          <button className="btn-ghost btn-sm" onClick={toggleTheme} aria-label="Toggle theme">
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            <span>{theme === 'dark' ? 'Light' : 'Dark'}</span>
          </button>
          {userId ? (
            <>
              <span className="user-chip">
                <User size={14} />
                <span>{userName || userId.slice(0, 8)}</span>
              </span>
              <button className="btn-outline btn-sm" onClick={logout}>
                <LogOut size={14} /> Logout
              </button>
            </>
          ) : (
            <button className="btn-primary btn-sm" onClick={() => navigate('/login')}>
              <User size={14} /> Login
            </button>
          )}
        </div>
      </header>

      <main className="main-content topnav-content">
        <Outlet />
      </main>
    </div>
  );
}
