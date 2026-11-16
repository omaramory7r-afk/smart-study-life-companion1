# Smart Study & Life Companion — v0.5 student accounts milestone

Arabic-first student productivity web app with a FastAPI backend and a responsive glass-style interface. This milestone adds student registration/login and account-owned study schedules on top of the v0.4 scheduling engine.

## Deployment on Render

This repository includes `render.yaml` for a single web service in Frankfurt. It uses a persistent disk for SQLite and Render-generated `SSC_AUTH_SECRET`. To deploy, push this project to a GitHub/GitLab repository, then create a Render Blueprint from that repository and review the resources and pricing before applying. The persistent disk is a paid resource on Render; remove `disk` only if you accept ephemeral data loss on restarts/redeploys. Configure `OPENAI_API_KEY` in the Render service environment only if AI endpoints are needed. Do not put secrets in source control.

## 1) Requirements

- Python 3.10+
- A modern browser
- An OpenAI API key only for AI-powered lecture/quiz/assistant features. The study scheduler does **not** require an AI key.

## 2) Run locally

```bash
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload
```

Open **http://127.0.0.1:8000**. Do not open `frontend/index.html` as a `file://` page; the frontend calls the local API.

The scheduler creates `backend/data/schedule.db` automatically. Set `SSC_DATABASE_PATH` in `backend/.env` to choose another SQLite path.

## 3) Student accounts (v0.5)

- Register with name, email and password (minimum 8 characters).
- Sign in and sign out from the Arabic-first interface.
- Passwords are stored as PBKDF2-HMAC-SHA256 hashes with per-user random salts; plaintext passwords are never stored.
- The API issues a signed bearer token valid for 7 days.
- Study plans are owned by the authenticated user. A student cannot read another student's saved plans, including by guessing a plan ID.
- Existing v0.4 schedule rows are migrated with a nullable `user_id`; they are not exposed to newly registered accounts.

Before public deployment, set a unique, long random `SSC_AUTH_SECRET` in `backend/.env`, use HTTPS, configure backups and rate limiting, and review the full application for privacy/security. The built-in auth is a development MVP, not a substitute for a production identity provider or security review.

## 4) Smart schedule engine (v0.4)

The planner accepts:
- Subjects: `id`, `name`, optional color and weekly target.
- Tasks: subject link, title, required minutes, priority (1–5), difficulty (1–5), deadline day (0 Sunday … 6 Saturday), and optional deadline time (`HH:MM`).
- Availability windows: day, start and end.
- Fixed commitments: day, start, end and optional label. Overlapping commitments are merged and removed from study availability.
- Configurable minimum/maximum session length and break duration.

The deterministic scheduling engine:
- Rejects overlapping availability windows.
- Subtracts fixed commitments from free windows.
- Estimates available minutes and slack before each task deadline.
- Orders work using deadline slack, priority, difficulty and deadline.
- Splits work into sessions while observing minimum/maximum session lengths and breaks.
- Never schedules a session after its task's deadline time.
- Reports unscheduled minutes and why they could not fit.
- Returns task-level pressure analysis and total schedule coverage.

Task input format in the web UI (one task per line):

`Subject | Task | Duration minutes | Priority 1-5 | Deadline day 0-6 | Deadline time (optional) | Difficulty 1-5 (optional)`

Availability input (one period per line):

`Day 0-6 | Start HH:MM | End HH:MM`

Fixed commitments (optional):

`Day 0-6 | Start HH:MM | End HH:MM | Label (optional)`

Example:

```text
Chemistry | Review chapter 1 | 90 | 5 | 2 | 18:00 | 4
Biology | Lecture questions | 60 | 4 | 3 | 21:00 | 3
```

Every generated schedule is automatically stored in SQLite. The UI can restore the latest saved plan from the server. Inputs are also cached in the current browser for convenience.

## 5) Schedule API

- `POST /api/auth/register` and `POST /api/auth/login` — create account and sign in.
- `GET /api/auth/me` — validate the current session.
- `POST /api/schedule/generate` — validate, generate and persist a plan.
- `GET /api/schedule/plans/latest` — retrieve the latest saved plan and its original request.
- `GET /api/schedule/plans/{plan_id}` — retrieve a specific saved plan.

## 6) Existing connected features

- API health indicator.
- Multiple-file extraction in visible order (PDF, DOCX, TXT, MD, CSV).
- AI lecture explanation and translation.
- Summary and text mind-map outline.
- Quiz generation up to 45 questions.
- Objective quiz feedback and AI-assisted essay evaluation.
- General AI chat, assignment help, project planning and presentation outline generation.
- Browser-local notebooks and theme preference.

## 7) Current boundaries

- Schedule persistence is a single-installation SQLite store. There are no user accounts or per-user authorization yet; do not expose this build as a public multi-user service until authentication and ownership checks are implemented.
- Authentication currently protects schedule data only; notebooks and several existing study tools remain browser-local or stateless and are not yet cloud-synced per account.
- The planner uses a seven-day relative week (Sunday = 0) rather than calendar dates/time zones. Deadline times are local clock values within that week.
- A fixed commitment only blocks time where it intersects a declared availability window.
- The AI endpoints require a valid server-side `OPENAI_API_KEY` in `backend/.env`. Never put the key in frontend code or send it in chat.
- Scanned/image-only PDFs are not OCR'd yet.
- Notebooks remain browser-local; no cloud sync yet.
- Presentation generation currently returns a slide outline, not a downloadable `.pptx` file.
- Background push reminders, prayer-time provider, complete interface localization, and study-group collaboration are not implemented yet.

## 8) Tests

Run the scheduler, authentication, account-isolation and API tests:

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

Tests cover priority ordering, insufficient time, overlapping availability, fixed commitments, changed availability, deadline clock limits, and saving/loading a plan through the API.

## 9) Security

- AI keys are read only by the backend from `.env`.
- Uploaded documents are processed in memory and are not written to disk by the extractor.
- Do not commit `.env` or the local SQLite database.
