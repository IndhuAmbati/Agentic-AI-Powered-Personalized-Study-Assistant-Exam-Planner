import React from 'react';
import { Routes, Route } from 'react-router-dom';
import LayoutShell from './components/LayoutShell';
import HomeDashboard from './pages/HomeDashboard';
import Profile from './pages/SimpleProfile';
import Login from './pages/Login';
import Schedule from './pages/Schedule';
import Progress from './pages/Progress';
import Quiz from './pages/Quiz';
import Chatbot from './pages/Chatbot';
import MindMap from './pages/MindMap';

export default function App() {
  return (
    <Routes>
      <Route element={<LayoutShell />}>
        <Route path="/" element={<HomeDashboard />} />
        <Route path="/login" element={<Login />} />
        <Route path="/profile" element={<Profile />} />
        <Route path="/schedule" element={<Schedule />} />
        <Route path="/progress" element={<Progress />} />
        <Route path="/mindmap" element={<MindMap />} />
        <Route path="/quiz" element={<Quiz />} />
        <Route path="/chatbot" element={<Chatbot />} />
      </Route>
    </Routes>
  );
}
