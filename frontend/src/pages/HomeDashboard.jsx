import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useUser } from '../context/UserContext';
import {
  BrainCircuit,
  CalendarDays,
  FileQuestion,
  MessageSquareText,
  Sparkles,
  Target,
  UserCircle2,
} from 'lucide-react';

const featureCards = [
  {
    title: 'Schedule',
    body: 'Plan your study time clearly and keep each day focused.',
    icon: CalendarDays,
    route: '/schedule',
    tone: 'schedule',
  },
  {
    title: 'Progress',
    body: 'Track quiz performance and learning progress visually.',
    icon: Target,
    route: '/progress',
    tone: 'progress',
  },
  {
    title: 'Mind Map',
    body: 'Visualize concepts and understand topic connections faster.',
    icon: BrainCircuit,
    route: '/mindmap',
    tone: 'mindmap',
  },
  {
    title: 'Quiz',
    body: 'Practice topic-wise tests and improve your score quickly.',
    icon: FileQuestion,
    route: '/quiz',
    tone: 'quiz',
  },
  {
    title: 'Chatbot',
    body: 'Ask doubts anytime and get quick study help.',
    icon: MessageSquareText,
    route: '/chatbot',
    tone: 'chatbot',
  },
  {
    title: 'Profile',
    body: 'Manage your topics, preferences, and study setup.',
    icon: UserCircle2,
    route: '/profile',
    tone: 'profile',
  },
];

export default function HomeDashboard() {
  const { userId } = useUser();
  const navigate = useNavigate();

  const goTo = (route) => navigate(userId ? route : '/login');

  return (
    <div className="page page-wide home-dashboard-classic">
      <section className="home-dashboard-banner">
        <span className="home-dashboard-kicker">
          <Sparkles size={14} />
          Study Assistant
        </span>
        <h1>Plan, Learn &amp; Succeed</h1>
        <p>Your all-in-one study assistant dashboard</p>
      </section>

      <section className="home-dashboard-features">
        <div className="home-dashboard-section-head">
          <h2>Features</h2>
          <span>{userId ? 'Open any tool and continue studying.' : 'Sign in to use the tools.'}</span>
        </div>

        <div className="home-dashboard-feature-grid">
          {featureCards.map((card) => (
            <button
              key={card.title}
              className={`home-dashboard-feature-card home-dashboard-feature-card-${card.tone}`}
              onClick={() => goTo(card.route)}
            >
              <div className="home-dashboard-feature-top">
                <div className="home-dashboard-feature-icon">
                  <card.icon size={18} />
                </div>
                <span>{userId ? 'Open' : 'Login'}</span>
              </div>
              <strong>{card.title}</strong>
              <p>{card.body}</p>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
