import axios from 'axios';

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '/api';

const api = axios.create({
  baseURL: apiBaseUrl,
  headers: { 'Content-Type': 'application/json' },
  timeout: 30000,
});

// ── Users ───────────────────────────────────────────────────────────
export const createUser = (data) => api.post('/users', data);
export const getUser = (id) => api.get(`/users/${id}`);
export const updateUser = (id, data) => api.patch(`/users/${id}`, data);
export const registerUser = (data) =>
  api.post('/auth/register', data, { timeout: 120000 });
export const loginUser = (data) =>
  api.post('/auth/login', data, { timeout: 60000 });

// ── Subjects ────────────────────────────────────────────────────────
export const createSubject = (data) => api.post('/subjects', data);
export const listSubjects = (userId) => api.get(`/subjects/${userId}`);
export const updateSubject = (id, data) => api.patch(`/subjects/${id}`, data);
export const deleteSubject = (id) => api.delete(`/subjects/${id}`);
export const importSyllabus = (data) => api.post('/subjects/syllabus/import', data);
export const importSyllabusPdf = (formData) =>
  api.post('/subjects/syllabus/import-pdf', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  });
export const previewSyllabusPdf = (formData) =>
  api.post('/syllabus/preview-pdf', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  });
export const resetSyllabusData = (userId) => api.delete(`/syllabus/reset/${userId}`);
export const confirmSyllabusAndGenerate = (data) =>
  api.post('/syllabus/confirm-and-generate', data, { timeout: 300000 });

// ── Topics ──────────────────────────────────────────────────────────
export const createTopic = (data) => api.post('/topics', data);
export const listTopics = (subjectId) => api.get(`/topics/subject/${subjectId}`);
export const updateTopic = (id, data) => api.patch(`/topics/${id}`, data);
export const deleteTopic = (id) => api.delete(`/topics/${id}`);
export const readyTopicForQuizzes = (topicId, data) => api.post(`/topics/${topicId}/ready-quizzes`, data);

// ── Schedule ────────────────────────────────────────────────────────
export const generateSchedule = (data) => api.post('/schedule/generate', data);
export const generateScheduleFromSyllabusPdf = (formData) =>
  api.post('/schedule/generate-from-syllabus-pdf', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  });
export const getSchedule = (userId) => api.get(`/schedule/${userId}`);
export const completeEntry = (id) => api.patch(`/schedule/complete/${id}`);
export const skipEntry = (id) => api.post(`/schedule/skip/${id}`);

// ── Progress ────────────────────────────────────────────────────────
export const updateProgress = (data) => api.post('/progress/update', data);
export const getProgressDashboard = (userId) => api.get(`/progress/${userId}`);

// ── Quiz ────────────────────────────────────────────────────────────
export const generateQuiz = (data) => api.post('/quiz/generate', data);
export const submitQuiz = (data) => api.post('/quiz/submit', data);

// ── Agent ───────────────────────────────────────────────────────────
export const adaptPlan = (userId) => api.post(`/agent/adapt?user_id=${userId}`);
export const getInsights = (userId) => api.get(`/agent/insights/${userId}`);

// ── Chat ────────────────────────────────────────────────────────────
export const askChatbot = (data) => api.post('/chat/ask', data);
export const getChatHistory = (userId) => api.get(`/chat/history/${userId}`);

// ── Voice / Notifications ───────────────────────────────────────────
export const processVoiceCommand = (text) => api.post(`/voice/command?text=${encodeURIComponent(text)}`);
export const speak = (data) => api.post('/voice/speak', data);
export const sendNotification = (data) => api.post('/voice/notify', data);
export const getNotifications = (userId, unreadOnly = false) =>
  api.get(`/voice/notifications/${userId}?unread_only=${unreadOnly}`);
export const markNotificationsRead = (userId) => api.post(`/voice/notifications/${userId}/read`);

export default api;
