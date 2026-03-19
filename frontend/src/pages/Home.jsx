import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useUser } from '../context/UserContext';
import {
  BookOpen,
  CalendarDays,
  FileQuestion,
  MessageSquare,
  PlayCircle,
  Sparkles,
  Target,
} from 'lucide-react';

const FEATURE_CARDS = [
  {
    title: 'Exam Planner',
    body: 'Create schedules from your subjects or syllabus PDF and stay on track with real study sessions.',
    icon: CalendarDays,
    route: '/schedule',
  },
  {
    title: 'AI Study Assistant',
    body: 'Ask doubts, understand concepts, and get study help connected to your topics and plan.',
    icon: MessageSquare,
    route: '/chatbot',
  },
  {
    title: 'Progress Tracking',
    body: 'Measure completed topics, weak areas, revision status, and overall learning progress.',
    icon: Target,
    route: '/progress',
  },
  {
    title: 'Quiz Generator',
    body: 'Practice with topic-based quizzes linked to the same subjects you are studying.',
    icon: FileQuestion,
    route: '/quiz',
  },
  {
    title: 'Subjects Manager',
    body: 'Organize courses, priorities, exam dates, and topic lists before building your plan.',
    icon: BookOpen,
    route: '/subjects',
  },
  {
    title: 'Profile Hub',
    body: 'Manage your study identity, syllabus imports, timetable setup, and personal progress overview.',
    icon: Target,
    route: '/profile',
  },
];

export default function Home() {
  const { userId } = useUser();
  const navigate = useNavigate();

  const goPrimary = () => navigate(userId ? '/schedule' : '/login');

  return (
    <div className="page home landing-page">
      <section className="landing-hero">
        <div className="landing-eyebrow">
          <Sparkles size={15} />
          <span>StudyMate</span>
        </div>
        <h1>Your Smart Study Assistant &amp; Exam Planner</h1>
        <p>
          Plan smarter, study better, and connect schedules, syllabus imports, quizzes, chatbot
          help, subjects, and progress tracking in one place.
        </p>
        <div className="landing-hero-actions">
          <button className="btn btn-primary lg" onClick={goPrimary}>
            <PlayCircle size={16} />
            {userId ? 'Start Planning' : 'Get Started'}
          </button>
        </div>
      </section>

      <section className="landing-section">
        <h2>Features</h2>
        <div className="landing-feature-grid">
          {FEATURE_CARDS.map((card) => (
            <button
              key={card.title}
              className="landing-feature-card"
              onClick={() => navigate(card.route)}
            >
              <div className="landing-feature-icon">
                <card.icon size={18} />
              </div>
              <div>
                <strong>{card.title}</strong>
                <span>{card.body}</span>
              </div>
            </button>
          ))}
        </div>
      </section>

      <section className="landing-section landing-about">
        <h2>About</h2>
        <p>
          Study Assistant &amp; Exam Planner is designed for students who want one practical workspace
          for planning subjects, importing syllabus PDFs, generating schedules, taking quizzes,
          chatting for study help, and tracking progress over time.
        </p>
      </section>

      <section className="landing-cta">
        <div>
          <h3>Start Your Smart Study Journey Today</h3>
        </div>
        <button className="btn landing-cta-btn" onClick={goPrimary}>
          {userId ? 'Open Planner' : 'Join Now'}
        </button>
      </section>
    </div>
  );
}
