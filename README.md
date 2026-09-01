# mvp_vibecode_pipeline

a vibecoded app for vibecoders. An autonomous coding pipeline built from two lanes that close a loop with each other: one turns a ticket into a reviewed, merged PR; the other watches a running system and files the ticket that kicks the first one off.

## How it works

**Lane 1 — Implementation agent: ticket → PR → merged**
Listens for a Jira/Linear ticket assigned to it, reads and refines it, drafts a dev plan, writes the code and tests, opens a PR, then waits. When a GitHub review comes in, it triages each comment — pushes a code fix for some, replies for others — and loops until the PR is merged.

**Lane 2 — Detection agent: telemetry/logs → ticket**
Runs alongside a system this pipeline has been integrated into, watching its OpenTelemetry data or logs. When it spots unwanted behavior, it files a ticket in Jira/Linear with enough context for Lane 1 to act on.

The two lanes close a loop: bad behavior observed → ticket filed → agent implements a fix → PR reviewed and merged.

The app itself is **stateless**: no database, no persistent storage for business state. It never remembers "where a ticket is" between runs — it re-derives that from Linear/Jira and GitHub every time (ticket status, whether a PR is already linked, that PR's review/merge state), the same way a human would glance at the ticket and the PR to see what's next. The user's only interface into any of this is tickets and PRs/reports — not a dashboard this app owns.

**Prerequisite for Lane 1**: the ticket tool's native GitHub integration (Linear↔GitHub, or GitHub for Jira) must already be installed and connected on the project/repo being worked on — that's how ticket↔PR linking is derived, not a scheme this app invents. The pipeline checks this per ticket before acting and stops with a comment if it isn't connected.

**Status**: only the foundational LangChain + LangSmith scaffold exists so far (see [SKILLS.md](SKILLS.md)) — neither lane is built yet, and that scaffold predates the statelessness decision. Full design, state-derivation rules, and open questions: [CLAUDE.md](CLAUDE.md#target-architecture-two-lanes).

## Stack

- Django (project: `config`)
- LangChain for building/running agent chains (`agents` app, `agents/services.py`)
- LangSmith for tracing/observability of every chain run
- Django REST Framework for the API surface

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then fill in OPENAI_API_KEY and LANGCHAIN_API_KEY

python manage.py migrate
python manage.py createsuperuser   # optional, for /admin/
python manage.py runserver
```

## LangSmith tracing

Tracing is enabled purely through environment variables (see `.env.example`):

- `LANGCHAIN_TRACING_V2=true`
- `LANGCHAIN_API_KEY` — from [smith.langchain.com](https://smith.langchain.com)
- `LANGCHAIN_PROJECT` — project name runs are grouped under
- `LANGCHAIN_ENDPOINT` — defaults to the LangSmith cloud endpoint

Once set, every LangChain call made through `agents/services.py` is traced automatically — no code changes needed. The LangSmith run id for each request is also saved on the local `AgentRun` record, so you can jump from a row in `/admin/` straight to its trace.

## Try it

```bash
curl -X POST http://127.0.0.1:8000/api/agents/run/ \
  -H "Content-Type: application/json" \
  -d '{"prompt": "hello"}'
```

Returns the model's response plus the LangSmith run id. Each call is also persisted as an `AgentRun` (prompt, response, status, error, LangSmith run id) — visible in `/admin/`.

## Layout

- `config/` — Django project settings, root URLs
- `agents/` — the app: `models.py` (`AgentRun`), `services.py` (LangChain chain construction + invocation), `views.py` / `urls.py` (the `/api/agents/run/` endpoint)

## Docs

- [HUMANS.md](HUMANS.md) — what this project is, in plain language, for non-technical readers
- [SKILLS.md](SKILLS.md) — catalog of what the pipeline can actually do today (and what's planned but not built yet)
- [CLAUDE.md](CLAUDE.md) — architecture notes and conventions for working in this repo with Claude Code
