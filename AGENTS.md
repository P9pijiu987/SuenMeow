# Repository Guidelines

SuenMeow 2 independently rewrites a Discourse companion. Consult `docs/SPECIFICATION.md`, `docs/AGENTIC.md`, and `docs/ACCEPTANCE.md` for behavior, roadmap, and evidence; legacy is a behavioral reference only.

## Project Structure & Module Organization

- `backend/suenmeow/`: FastAPI endpoints, authentication, models, validation, services, adapters, worker, and CLI.
- `backend/tests/`: pytest API, integration, and send-safety tests.
- `frontend/src/`: React/TypeScript console and CSS; `frontend/index.html`: entry document.
- `deploy/` and `compose.yaml`: container images, gateway, and PostgreSQL deployment.
- `tools/`: deployment helpers; `docs/`: specifications, migration, and evidence.
- `secrets/`, `.env`, and `runtime/`: ignored local credentials and state. Never commit them.

## Build, Test, and Development Commands

Use Python 3.12+, Node 22+, and PostgreSQL for deployment:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/suenmeow init
.venv/bin/python -m pytest -q
cd frontend
npm ci
npm run dev
npm run build
```

Initialization creates a paused system. Use the API origin in `README.md`. `docker compose build` builds images; initialize explicitly before starting services. SQLite is for development only.

## Coding Style & Naming Conventions

Use four-space Python indentation, type hints, `snake_case` functions/modules, and `PascalCase` classes. React components use `PascalCase`; preserve the existing two-space TypeScript style. Keep HTTP operations asynchronous and external services behind adapters. No formatter or linter is currently configured; TypeScript checks run during build.

## Testing Guidelines

Name pytest files `test_*.py` and functions `test_*`; use pytest-asyncio for asynchronous cases. Use fake clients and isolated databases. Tests must never post to live forums. Verify authorization, CSRF, immutable publications, concurrent budget reservations, backlog baselines, cooldowns, uncertain sends, and private-memory boundaries. No numeric coverage threshold is configured.

Cover registration, grant revocation, quotas, and conflicts. `tools/check_workspace.py` probes isolated drafts; ignore credentials and clean up fixtures.

## Commit & Pull Request Guidelines

Use imperative subjects; legacy history has no enforced Conventional Commits. Describe changes, validation, migrations, and deployment impact; link issues and include screenshots. Redact credentials and private conversations.

## Security & Agent Instructions

Implement independently; never copy legacy source. All sends must use the shared gate and existing topic IDs. Skip backlog after startup/recovery; never retry uncertain sends or resend because memory failed. Enforce permissions server-side. Personal-topic imports require a verified author ID, source quotes, and bounded model calls; editors can import only their own public topics. Agent tools cannot grant send permissions; isolate private research and administrator sessions. Persona and personal-topic edits take effect on save unless administrator review is enabled (default off); system prompts and global arrangements remain drafts until administrator publication. Update acceptance evidence after verification and preserve recoverable deployment backups.
