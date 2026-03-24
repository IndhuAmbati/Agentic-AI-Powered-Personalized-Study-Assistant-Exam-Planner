# Agentic AI-Powered Personalized Study Assistant & Exam Planner

A modular, scalable system for personalized adaptive study planning with agentic AI capabilities. The system generates dynamic study schedules, monitors student progress, adapts plans in real time, and provides intelligent feedback and voice-based interaction.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│               React Frontend (Vite + React 18)              │
│  (Dashboard · Schedule · Quiz · Voice · Analytics · Agent)  │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP/REST (Vite proxy → :8000)
┌──────────────────────────▼──────────────────────────────────┐
│                    FastAPI Backend                           │
│  ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌───────────────┐   │
│  │ Schedule  │ │ Progress │ │  Quiz   │ │    Voice      │   │
│  │  Engine   │ │ Tracker  │ │ Module  │ │  Interaction  │   │
│  └─────┬────┘ └────┬─────┘ └────┬────┘ └───────┬───────┘   │
│        │           │            │               │           │
│  ┌─────▼───────────▼────────────▼───────────────▼───────┐   │
│  │              Adaptive AI Agent                        │   │
│  │         (Observe → Plan → Act → Reflect)              │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                   │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │              Database Layer (SQLAlchemy)               │   │
│  └───────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Project Structure

```
backend/
  app/
    main.py              # FastAPI application entry point
    config.py            # Configuration & settings
    database.py          # Database connection & session
    models/              # SQLAlchemy ORM models
    schemas/             # Pydantic request/response schemas
    api/                 # API route handlers
    services/            # Business logic
      scheduler.py       # Core scheduling algorithm
      progress.py        # Progress tracking
      adaptive_agent.py  # Agentic AI loop
      quiz_engine.py     # Quiz generation & evaluation
      voice.py           # Voice interaction
      notifications.py   # Notification system
frontend/
  package.json           # Node dependencies
  vite.config.js         # Vite dev server & proxy config
  index.html             # HTML entry point
  src/
    main.jsx             # React root with providers
    App.jsx              # Route definitions
    context/
      UserContext.jsx     # Global user state (React Context)
    services/
      api.js             # Axios API service layer
    components/
      Layout.jsx          # Sidebar navigation layout
      MetricCard.jsx      # Reusable metric display card
      RequireUser.jsx     # Auth guard component
    pages/
      Dashboard.jsx       # Overview & quick actions
      Profile.jsx         # Create / load user profile
      Subjects.jsx        # Subject & topic management
      Schedule.jsx        # Generate & view study schedule
      Progress.jsx        # Log sessions & analytics charts
      Quiz.jsx            # Generate, take & review quizzes
      Agent.jsx           # AI insights & adaptive re-planning
      Voice.jsx           # Voice commands & notifications
    styles/
      index.css           # Global styles
tests/                   # Test suite (pytest)
```

## Quick Start

### 1. Install Backend Dependencies

```bash
pip install -e .
```

### 2. Install Frontend Dependencies

```bash
cd frontend
npm install
cd ..
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env as needed
# For MySQL set DATABASE_URL to:
# mysql+aiomysql://root:password@127.0.0.1:3306/study_assistant
```

### 4. Start the Backend

```bash
uvicorn backend.app.main:app --reload --port 8000
```

### 5. Start the Frontend

```bash
cd frontend
npm run dev
```

The React app runs at **http://localhost:3000** and proxies API requests to the backend on port 8000.

### 6. Run Tests

```bash
pytest tests/ -v
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST   | `/api/users` | Create a user profile |
| POST   | `/api/auth/register` | Register login + profile |
| POST   | `/api/auth/login` | Login with email/password |
| GET    | `/api/users/{id}` | Get user profile |
| POST   | `/api/subjects` | Add a subject |
| GET    | `/api/subjects/{user_id}` | List user's subjects |
| POST   | `/api/topics` | Add a topic to a subject |
| POST   | `/api/schedule/generate` | Generate study schedule |
| POST   | `/api/schedule/generate-from-syllabus-pdf` | Upload syllabus PDF (Unit range), chunk with LangChain, and generate timetable |
| GET    | `/api/schedule/{user_id}` | Get current schedule |
| POST   | `/api/progress/update` | Update topic progress |
| GET    | `/api/progress/{user_id}` | Get progress dashboard |
| POST   | `/api/quiz/generate` | Generate a quiz |
| POST   | `/api/quiz/submit` | Submit quiz answers |
| GET    | `/api/quiz/history/{user_id}` | Get quiz history |
| POST   | `/api/agent/adapt` | Trigger adaptive re-planning |
| GET    | `/api/agent/insights/{user_id}` | Get AI insights |
| POST   | `/api/voice/command` | Process voice command |

## Core Algorithms

### Priority-Based Scheduling
The scheduler uses a weighted priority score combining:
- **Exam proximity** (higher weight as exams approach)
- **Topic difficulty** (harder topics get more time)
- **Current progress** (incomplete topics prioritized)
- **Spaced repetition** (revisit completed topics at intervals)

### Adaptive Agent Loop
The AI agent follows an Observe-Plan-Act-Reflect cycle:
1. **Observe**: Collect progress data, quiz scores, time spent
2. **Plan**: Identify weak areas, calculate new priorities
3. **Act**: Adjust schedule, suggest resources, generate quizzes
4. **Reflect**: Evaluate if changes improved performance

## License

MIT
