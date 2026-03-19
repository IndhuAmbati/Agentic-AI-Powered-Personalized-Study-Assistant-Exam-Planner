import React, { useEffect, useRef, useState } from 'react';
import RequireUser from '../components/RequireUser';
import { useUser } from '../context/UserContext';
import { askChatbot, getChatHistory } from '../services/api';
import toast from 'react-hot-toast';
import { Bot, SendHorizonal, User, Mic, MicOff, Play, Square, Trash2 } from 'lucide-react';

const STARTERS = [
  'What should I study next?',
  'List my subjects',
  'Show topics in Data Analytics',
  'How should I revise Unit 1?',
];

const SESSION_GAP_MINUTES = 45;
const SESSION_META_KEY = (userId) => `chatSessions:${userId || 'anon'}`;
const CURRENT_SESSION_KEY = (userId) => `chatCurrent:${userId || 'anon'}`;
const MSG_SESSION_KEY = (userId) => `chatMsgSession:${userId || 'anon'}`;

const WELCOME = {
  role: 'assistant',
  text: 'Ask me anything educational. I can chat naturally and also use your study data when relevant.',
  suggestions: STARTERS,
};

const modeLabel = (mode) => {
  switch (mode) {
    case 'openai':
      return 'OpenAI answer';
    case 'groq':
      return 'Groq answer';
    case 'education_clarification':
      return 'Need more educational context';
    case 'education_only':
      return 'Education-only restriction';
    case 'groq_fallback':
      return 'Educational answer';
    case 'openai_fallback':
      return 'Educational answer';
    case 'general_education':
      return 'Educational answer';
    case 'rule_based':
      return 'Educational answer';
    default:
      return mode ? `${mode} answer` : null;
  }
};

export default function Chatbot() {
  const { userId } = useUser();
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [listening, setListening] = useState(false);
  const [hasRecognition, setHasRecognition] = useState(false);
  const [hasSynthesis, setHasSynthesis] = useState(false);
  const [voices, setVoices] = useState([]);
  const [voiceId, setVoiceId] = useState('');
  const recognitionRef = useRef(null);
  const [allMessages, setAllMessages] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState('');
  const [sessionMeta, setSessionMeta] = useState([]);
  const [msgSessionMap, setMsgSessionMap] = useState({});
  const freshSessionRef = useRef(false);

  const loadSessionMeta = (uid) => {
    if (!uid || typeof window === 'undefined') return [];
    try {
      const raw = localStorage.getItem(SESSION_META_KEY(uid));
      const parsed = JSON.parse(raw || '[]');
      if (Array.isArray(parsed)) {
        return parsed
          .filter((s) => s?.id && s?.startedAt)
          .map((s) => ({ id: s.id, startedAt: Number(s.startedAt) || 0 }))
          .sort((a, b) => a.startedAt - b.startedAt);
      }
    } catch (e) {
      console.warn('Failed to load session meta', e);
    }
    return [];
  };

  const saveSessionMeta = (uid, meta) => {
    if (!uid || typeof window === 'undefined') return;
    try {
      localStorage.setItem(SESSION_META_KEY(uid), JSON.stringify(meta));
    } catch (e) {
      console.warn('Failed to save session meta', e);
    }
  };

  const saveCurrentSession = (uid, sid) => {
    if (!uid || typeof window === 'undefined') return;
    try {
      localStorage.setItem(CURRENT_SESSION_KEY(uid), sid);
    } catch {}
  };

  const loadCurrentSession = (uid) => {
    if (!uid || typeof window === 'undefined') return '';
    try {
      return localStorage.getItem(CURRENT_SESSION_KEY(uid)) || '';
    } catch {
      return '';
    }
  };

  const loadMsgSessionMap = (uid) => {
    if (!uid || typeof window === 'undefined') return {};
    try {
      const raw = localStorage.getItem(MSG_SESSION_KEY(uid));
      const parsed = JSON.parse(raw || '{}');
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  };

  const saveMsgSessionMap = (uid, map) => {
    if (!uid || typeof window === 'undefined') return;
    try {
      localStorage.setItem(MSG_SESSION_KEY(uid), JSON.stringify(map));
    } catch {}
  };

  const startFreshSession = () => {
    if (!userId) return;
    const newId = `session-${Date.now()}`;
    setCurrentSessionId(newId);
    saveCurrentSession(userId, newId);
    setSessionMeta((prev) => {
      const exists = prev.some((s) => s.id === newId);
      const next = exists ? prev : [...prev, { id: newId, startedAt: Date.now() }];
      saveSessionMeta(userId, next);
      return next;
    });
  };

  const deleteSession = (sessionId) => {
    if (!sessionId) return;
    // Remove session meta
    setSessionMeta((prev) => {
      const nextMeta = prev.filter((s) => s.id !== sessionId);
      saveSessionMeta(userId, nextMeta);
      return nextMeta;
    });
    // Remove messages for that session
    setAllMessages((prev) => prev.filter((m) => m.sessionId !== sessionId));
    // Remove message-session mappings
    setMsgSessionMap((prev) => {
      const next = { ...prev };
      Object.keys(next).forEach((mid) => {
        if (next[mid] === sessionId) delete next[mid];
      });
      saveMsgSessionMap(userId, next);
      return next;
    });
    // Pick a new current session
    setCurrentSessionId((prevCurrent) => {
      if (prevCurrent !== sessionId) return prevCurrent;
      const remainingMeta = loadSessionMeta(userId).filter((m) => m.id !== sessionId);
      const fallback = remainingMeta[remainingMeta.length - 1]?.id || '';
      saveCurrentSession(userId, fallback);
      return fallback;
    });
  };

  const getVisibleMessages = () =>
    allMessages.filter(
      (m) => m.sessionId === currentSessionId || (!currentSessionId && !m.sessionId),
    );

  const visibleMessages = getVisibleMessages();

  const sendMessage = async (rawText) => {
    const text = rawText.trim();
    if (!text || !userId || loading) return;

    const sessionId = currentSessionId || `session-${Date.now()}`;
    setCurrentSessionId(sessionId);
    saveCurrentSession(userId, sessionId);
    setSessionMeta((prev) => {
      const exists = prev.some((s) => s.id === sessionId);
      const next = exists ? prev : [...prev, { id: sessionId, startedAt: Date.now() }];
      saveSessionMeta(userId, next);
      return next;
    });

    const userMessage = { id: `local-${Date.now()}-u`, role: 'user', text, created_at: new Date().toISOString(), sessionId };
    const nextVisible = [
      ...allMessages.filter((m) => m.sessionId === sessionId || (!sessionId && !m.sessionId)),
      userMessage,
    ];
    const nextAll = [...allMessages, userMessage];
    setAllMessages(nextAll);
    setMsgSessionMap((prev) => {
      const updated = { ...prev, [userMessage.id]: sessionId };
      saveMsgSessionMap(userId, updated);
      return updated;
    });
    setInput('');
    setLoading(true);
    try {
      const history = nextVisible
        .filter((message) => message.role === 'user' || message.role === 'assistant')
        .slice(0, -1)
        .map((message) => ({
          role: message.role,
          content: message.text,
        }));
      const { data } = await askChatbot({ user_id: userId, message: text, history });
      setAllMessages((prev) => [
        ...prev,
        {
          id: data.id || `local-${Date.now()}-a`,
          role: 'assistant',
          text: data.answer,
          suggestions: data.suggestions || [],
          meta: modeLabel(data.mode) || 'Educational answer',
          created_at: new Date().toISOString(),
          sessionId,
        },
      ]);
      setMsgSessionMap((prev) => {
        const respId = data.id || `local-${Date.now()}-a`;
        const updated = { ...prev, [respId]: sessionId };
        saveMsgSessionMap(userId, updated);
        return updated;
      });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Could not get chatbot response');
    } finally {
      setLoading(false);
    }
  };

  // Voice input setup
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    setHasSynthesis(Boolean(window.speechSynthesis));
    if (!SR) return;
    const rec = new SR();
    rec.lang = 'en-US';
    rec.continuous = false;
    rec.interimResults = false;
    rec.onresult = (e) => {
      const transcript = e.results?.[0]?.[0]?.transcript || '';
      if (transcript) {
        setInput((prev) => (prev ? `${prev} ${transcript}` : transcript));
        sendMessage(transcript);
      }
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recognitionRef.current = rec;
    setHasRecognition(true);
    return () => {
      rec.onresult = null;
      rec.onend = null;
      rec.onerror = null;
      rec.stop();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleListening = () => {
    if (!hasRecognition || !recognitionRef.current) {
      toast.error('Browser speech recognition not available.');
      return;
    }
    if (listening) {
      recognitionRef.current.stop();
      setListening(false);
    } else {
      try {
        recognitionRef.current.start();
        setListening(true);
      } catch (err) {
        setListening(false);
        toast.error('Mic is blocked. Allow microphone access.');
      }
    }
  };

  // Text-to-speech helpers (manual play/stop only)
  const speakText = (text) => {
    if (!hasSynthesis || !text) return;
    const utter = new SpeechSynthesisUtterance(text);
    const selected = voices.find((v) => v.voiceURI === voiceId) || voices[0];
    if (selected) utter.voice = selected;
    utter.lang = selected?.lang || 'en-US';
    utter.rate = 1;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utter);
  };

  const stopSpeaking = () => {
    if (!hasSynthesis) return;
    window.speechSynthesis.cancel();
  };

  // Load available voices
  useEffect(() => {
    if (!hasSynthesis) return;
    const loadVoices = () => {
      const v = window.speechSynthesis?.getVoices() || [];
      if (v.length) {
        setVoices(v);
        if (!voiceId) {
          const en = v.find((voice) => voice.lang.toLowerCase().startsWith('en'));
          setVoiceId(en?.voiceURI || v[0].voiceURI);
        }
      }
    };
    loadVoices();
    window.speechSynthesis?.addEventListener('voiceschanged', loadVoices);
    return () => window.speechSynthesis?.removeEventListener('voiceschanged', loadVoices);
  }, [hasSynthesis, voiceId]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    await sendMessage(input);
  };

  // Load chat history for the user
  useEffect(() => {
    let active = true;
    const loadHistory = async () => {
      if (!userId) return;
      try {
        const storedMeta = loadSessionMeta(userId);
        const storedMsgMap = loadMsgSessionMap(userId);
        setSessionMeta(storedMeta);
        setMsgSessionMap(storedMsgMap);
        const { data } = await getChatHistory(userId);
        if (!active) return;
        if (Array.isArray(data) && data.length) {
          // Assign messages into sessions using stored meta first, then fall back to gaps
          const withSessions = [];
          let sessionId = '';
          let lastTs = 0;
          let meta = storedMeta;
          const ensureMetaFromGaps = () => {
            const inferred = [];
            let prev = 0;
            data.forEach((item, idx) => {
              const ts = new Date(item.created_at).getTime() || 0;
              if (!prev || ts - prev > SESSION_GAP_MINUTES * 60 * 1000) {
                inferred.push({ id: `session-${ts || idx}`, startedAt: ts || Date.now() });
              }
              prev = ts;
            });
            meta = inferred.length ? inferred : [{ id: `session-${Date.now()}`, startedAt: Date.now() }];
          };

          if (!meta.length) {
            ensureMetaFromGaps();
          }

          const metaSorted = [...meta].sort((a, b) => a.startedAt - b.startedAt);

          const updatedMsgMap = { ...storedMsgMap };

          data.forEach((item, idx) => {
            const ts = new Date(item.created_at).getTime() || 0;
            const mappedSession = updatedMsgMap[item.id];
            const matching = mappedSession
              ? metaSorted.find((m) => m.id === mappedSession) || null
              : metaSorted.findLast((m) => ts >= m.startedAt);
            if (matching) {
              sessionId = matching.id;
            } else {
              if (!sessionId) sessionId = metaSorted[0]?.id || `session-${ts || idx}`;
            }
            lastTs = ts;
            withSessions.push({
              id: item.id,
              role: item.role,
              text: item.content,
              meta: modeLabel(item.mode),
              created_at: item.created_at,
              sessionId,
            });
            if (!updatedMsgMap[item.id]) {
              updatedMsgMap[item.id] = sessionId;
            }
          });
          // If we inferred from gaps, persist meta for next load
          if (!storedMeta.length) {
            const firstBySession = new Map();
            withSessions.forEach((m) => {
              if (!firstBySession.has(m.sessionId)) {
                firstBySession.set(m.sessionId, new Date(m.created_at).getTime() || Date.now());
              }
            });
            const newMeta = Array.from(firstBySession.entries()).map(([id, startedAt]) => ({ id, startedAt }));
            saveSessionMeta(userId, newMeta);
            setSessionMeta(newMeta);
            meta = newMeta;
          } else {
            setSessionMeta(meta);
          }
          // no placeholder injection; empty sessions stay empty
          // Sort messages chronologically to keep ordering clean
          withSessions.sort(
            (a, b) => new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime(),
          );
          setAllMessages(withSessions);
          setMsgSessionMap(updatedMsgMap);
          saveMsgSessionMap(userId, updatedMsgMap);
          const remembered = loadCurrentSession(userId);
          const fallbackSession = withSessions[withSessions.length - 1]?.sessionId || '';
          const chosen = withSessions.some((m) => m.sessionId === remembered) ? remembered : fallbackSession;
          setCurrentSessionId(chosen);
          saveCurrentSession(userId, chosen);
        } else {
          const freshSessionId = `session-${Date.now()}`;
          const metaArr = [{ id: freshSessionId, startedAt: Date.now() }];
          setAllMessages([]);
          setCurrentSessionId(freshSessionId);
          saveSessionMeta(userId, metaArr);
          setSessionMeta(metaArr);
          saveCurrentSession(userId, freshSessionId);
        }
        if (!freshSessionRef.current) {
          startFreshSession();
          freshSessionRef.current = true;
        }
      } catch {
        // fallback to empty new session
        const freshSessionId = `session-${Date.now()}`;
        setAllMessages([]);
        setCurrentSessionId(freshSessionId);
        const metaArr = [{ id: freshSessionId, startedAt: Date.now() }];
        saveSessionMeta(userId, metaArr);
        setSessionMeta(metaArr);
        saveCurrentSession(userId, freshSessionId);
        freshSessionRef.current = true;
      }
    };
    loadHistory();
    return () => {
      active = false;
    };
  }, [userId]);

  const startNewChat = () => {
    const newId = `session-${Date.now()}`;
    setCurrentSessionId(newId);
    saveCurrentSession(userId, newId);
    const updatedMeta = [...loadSessionMeta(userId), { id: newId, startedAt: Date.now() }];
    saveSessionMeta(userId, updatedMeta);
    setSessionMeta(updatedMeta);
    // keep message map; no placeholder messages
  };

  const sessions = (() => {
    const metaMap = new Map(sessionMeta.map((m) => [m.id, m.startedAt]));
    const grouped = new Map();
    allMessages.forEach((m) => {
      if (!grouped.has(m.sessionId)) grouped.set(m.sessionId, []);
      grouped.get(m.sessionId).push(m);
    });
    const ids = new Set([...metaMap.keys(), ...grouped.keys()]);
    const entries = Array.from(ids).map((id) => {
      const msgs = grouped.get(id) || [];
      const firstUser = msgs.find((m) => m.role === 'user');
      const title = firstUser?.text?.slice(0, 48) || 'New chat';
      const latestTs = msgs.length
        ? Math.max(...msgs.map((m) => new Date(m.created_at || Date.now()).getTime()))
        : metaMap.get(id) || Date.now();
      const startedAt = metaMap.get(id) || (msgs[0] ? new Date(msgs[0].created_at).getTime() : Date.now());
      return { id, title, latestTs, startedAt };
    });
    return entries.sort((a, b) => b.startedAt - a.startedAt);
  })();

  return (
    <RequireUser>
      <div className="page chatbot-page">
        <h1 className="page-title">Educational Chatbot</h1>

        <div className="card card-info">
          Ask concept doubts, exam questions, revision help, or study-planning questions. The chatbot can answer general educational questions and also use your saved subjects, topics, and timetable when relevant.
        </div>

        {!hasRecognition && (
          <div className="card card-warning" style={{ marginBottom: '1rem' }}>
            Voice input is not available in this browser. Use Chrome/Edge on desktop for speech-to-text.
          </div>
        )}

        <div className="chat-shell chat-shell-grid">
          <aside className="card chat-sidebar">
            <div className="chat-sidebar-head">
              <h3>Chats</h3>
              <button className="btn btn-sm btn-primary" type="button" onClick={startNewChat}>
                New
              </button>
            </div>
            <div className="chat-session-list">
              {sessions.map((session) => (
                <button
                  key={session.id}
                  type="button"
                  className={`chat-session-btn ${session.id === currentSessionId ? 'active' : ''}`}
                  onClick={() => setCurrentSessionId(session.id)}
                >
                  <span className="chat-session-title">{session.title}</span>
                  <span className="chat-session-time">
                    {new Date(session.latestTs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                  <span
                    className="chat-session-delete"
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteSession(session.id);
                    }}
                    title="Delete chat"
                  >
                    <Trash2 size={14} />
                  </span>
                </button>
              ))}
              {sessions.length === 0 && <div className="text-muted">No chats yet.</div>}
            </div>
          </aside>

          <div className="chat-main">
            <div className="chat-messages card">
              {visibleMessages.map((message, index) => (
                <div
                  key={`${message.role}-${index}`}
                  className={`chat-message ${message.role === 'user' ? 'chat-message-user' : 'chat-message-assistant'}`}
                >
                  <div className="chat-avatar">
                    {message.role === 'user' ? <User size={16} /> : <Bot size={16} />}
                  </div>
                  <div className="chat-bubble">
                    {message.meta && <div className="chat-meta">{message.meta}</div>}
                    <div style={{ whiteSpace: 'pre-wrap' }}>{message.text}</div>
                    {message.suggestions?.length > 0 && (
                      <div className="chat-suggestions">
                        {message.suggestions.map((suggestion) => (
                          <button
                            key={suggestion}
                            type="button"
                            className="chat-suggestion"
                            onClick={() => sendMessage(suggestion)}
                            disabled={loading}
                          >
                            {suggestion}
                          </button>
                        ))}
                      </div>
                    )}
                    {hasSynthesis && message.role === 'assistant' && (
                      <div className="chat-voice-controls" style={{ marginTop: '0.5rem', gap: '0.4rem', flexWrap: 'wrap' }}>
                        <button
                          type="button"
                          className="btn btn-outline"
                          onClick={() => speakText(message.text || '')}
                          disabled={loading}
                          title="Play voice for this reply"
                        >
                          <Play size={16} />
                          Play
                        </button>
                        <button
                          type="button"
                          className="btn btn-outline"
                          onClick={stopSpeaking}
                          disabled={loading}
                          title="Stop voice playback"
                        >
                          <Square size={16} />
                          Stop
                        </button>
                        {voices.length > 0 && (
                          <select
                            className="voice-select"
                            value={voiceId}
                            onChange={(e) => setVoiceId(e.target.value)}
                            aria-label="Voice selection"
                            style={{ minWidth: 180 }}
                          >
                            {voices.slice(0, 20).map((voice) => (
                              <option key={voice.voiceURI} value={voice.voiceURI}>
                                {voice.name} ({voice.lang})
                              </option>
                            ))}
                          </select>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {loading && (
                <div className="chat-message chat-message-assistant">
                  <div className="chat-avatar">
                    <Bot size={16} />
                  </div>
                  <div className="chat-bubble">Thinking...</div>
                </div>
              )}
            </div>

            <form className="card chat-input-row" onSubmit={handleSubmit}>
              <div className="chat-input-controls">
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="Ask an educational question..."
                />
                <div className="chat-voice-controls">
                  <button
                    type="button"
                    className={`btn btn-outline ${listening ? 'active' : ''}`}
                    onClick={toggleListening}
                    title={hasRecognition ? 'Voice input' : 'Voice not available'}
                    disabled={loading || !hasRecognition}
                  >
                    {listening ? <MicOff size={16} /> : <Mic size={16} />}
                    {listening ? 'Listening' : 'Speak'}
                  </button>
                </div>
              </div>
              <button className="btn btn-primary" type="submit" disabled={loading || !input.trim()}>
                <SendHorizonal size={16} />
                Ask
              </button>
            </form>
          </div>
        </div>
      </div>
    </RequireUser>
  );
}
