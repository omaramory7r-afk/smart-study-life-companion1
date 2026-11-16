# Changelog

## v0.4 — Smart scheduling engine and persistence

- Added structured subject, task, availability and fixed-commitment models.
- Added optional deadline clock times and task difficulty.
- Replaced the simple deadline-first loop with a deadline-slack, priority and difficulty aware deterministic scheduler.
- Added fixed commitment subtraction, session-length constraints, break handling and unscheduled-work explanations.
- Added per-task capacity/slack analysis and richer summary metrics.
- Added SQLite persistence for generated schedules and API endpoints to restore the latest or a specific saved plan.
- Updated the planner UI with deadline time, difficulty, fixed commitments, minimum session length and server restore controls.
- Added automated unit and API tests for the core scheduling scenarios.

## v0.3 — Weekly study scheduler

- Added a first deterministic weekly scheduling endpoint.
- Added basic schedule input persistence in browser local storage.
- Added a weekly schedule display and basic overlap validation.

## v0.2 — AI workflow integration

- Connected lecture explanation, translation, summarization, quiz, grading and assistant endpoints.
- Added ordered multiple-file text extraction.
- Added a browser-local notebook prototype and theme preference.


## v0.5 — Student accounts
- Added account registration and login endpoints and a simple Arabic sign-in/register screen.
- Passwords are hashed using PBKDF2-HMAC-SHA256 with individual salts.
- Added signed 7-day bearer sessions.
- Added per-user ownership to saved study schedules and protected schedule reads/writes.
- Added a database migration for existing schedule databases.
- Added tests for authentication, duplicate registration, login, schedule persistence and cross-account privacy.
