import React, { useState } from 'react';
import { useUser } from '../context/UserContext';
import RequireUser from '../components/RequireUser';
import {
  processVoiceCommand,
  speak,
  sendNotification,
  getNotifications,
  markNotificationsRead,
} from '../services/api';
import toast from 'react-hot-toast';
import { Mic, Volume2, Bell, CheckCheck } from 'lucide-react';

export default function Voice() {
  const { userId } = useUser();

  // Voice command
  const [cmdText, setCmdText] = useState('');
  const [cmdResult, setCmdResult] = useState(null);

  // TTS
  const [ttsText, setTtsText] = useState('');

  // Notifications
  const [notifTitle, setNotifTitle] = useState('');
  const [notifBody, setNotifBody] = useState('');
  const [notifications, setNotifications] = useState([]);

  const handleCommand = async () => {
    if (!cmdText.trim()) return;
    try {
      const { data } = await processVoiceCommand(cmdText);
      setCmdResult(data);
    } catch {
      toast.error('Error processing command');
    }
  };

  const handleSpeak = async () => {
    if (!ttsText.trim()) return;
    try {
      await speak({ text: ttsText });
      toast.success('Speaking…');
    } catch {
      toast.error('TTS error');
    }
  };

  const handleSendNotif = async (e) => {
    e.preventDefault();
    try {
      await sendNotification({ user_id: userId, title: notifTitle, body: notifBody });
      toast.success('Notification sent!');
      setNotifTitle('');
      setNotifBody('');
    } catch {
      toast.error('Error');
    }
  };

  const loadNotifications = async () => {
    try {
      const { data } = await getNotifications(userId);
      setNotifications(data);
    } catch {}
  };

  const handleMarkRead = async () => {
    try {
      const { data } = await markNotificationsRead(userId);
      toast.success(`Marked ${data.marked_read} as read`);
      loadNotifications();
    } catch {}
  };

  return (
    <RequireUser>
      <div className="page">
        <h1 className="page-title">🔊 Voice Interaction &amp; Notifications</h1>

        {/* Voice commands */}
        <div className="card">
          <h3>
            <Mic size={18} /> Voice / Text Commands
          </h3>
          <p className="text-muted">
            Supported: <code>generate schedule</code>, <code>show schedule</code>,{' '}
            <code>show progress</code>, <code>start quiz</code>, <code>adapt plan</code>,{' '}
            <code>help</code>
          </p>
          <div className="form-row" style={{ alignItems: 'flex-end' }}>
            <div className="form-group" style={{ flex: 1 }}>
              <label>Type or speak a command</label>
              <input value={cmdText} onChange={(e) => setCmdText(e.target.value)} />
            </div>
            <button className="btn btn-primary" onClick={handleCommand}>
              <Mic size={14} /> Process
            </button>
          </div>
          {cmdResult && (
            <div className="json-display" style={{ marginTop: '0.75rem' }}>
              <p>
                <strong>Parsed command:</strong> <code>{cmdResult.command}</code>
              </p>
              <p>
                <strong>Raw:</strong> {cmdResult.raw}
              </p>
            </div>
          )}
        </div>

        {/* TTS */}
        <div className="card" style={{ marginTop: '1.5rem' }}>
          <h3>
            <Volume2 size={18} /> Text-to-Speech
          </h3>
          <div className="form-group">
            <textarea
              rows={2}
              value={ttsText}
              onChange={(e) => setTtsText(e.target.value)}
              placeholder="Enter text to speak…"
            />
          </div>
          <button className="btn btn-primary" onClick={handleSpeak}>
            🗣️ Speak
          </button>
        </div>

        {/* Notifications */}
        <div className="card" style={{ marginTop: '1.5rem' }}>
          <h3>
            <Bell size={18} /> Notifications
          </h3>

          <div className="two-col">
            <form className="form" onSubmit={handleSendNotif}>
              <h4>Send Notification</h4>
              <div className="form-group">
                <label>Title</label>
                <input value={notifTitle} onChange={(e) => setNotifTitle(e.target.value)} />
              </div>
              <div className="form-group">
                <label>Message</label>
                <textarea rows={2} value={notifBody} onChange={(e) => setNotifBody(e.target.value)} />
              </div>
              <button className="btn btn-primary" type="submit">
                Send
              </button>
            </form>

            <div>
              <h4>Inbox</h4>
              <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem' }}>
                <button className="btn btn-outline btn-sm" onClick={loadNotifications}>
                  📬 Load
                </button>
                <button className="btn btn-outline btn-sm" onClick={handleMarkRead}>
                  <CheckCheck size={12} /> Mark All Read
                </button>
              </div>
              {notifications.length === 0 ? (
                <p className="text-muted">No notifications.</p>
              ) : (
                <ul className="notif-list">
                  {notifications.map((n, i) => (
                    <li key={i} className={n.read ? 'read' : 'unread'}>
                      <strong>{n.title}</strong>
                      <span>{n.body}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      </div>
    </RequireUser>
  );
}
