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

**Status**: Lane 1's first four steps (listen, verify the GitHub integration, read, refine) are implemented for Linear, and step 9's listening half (recognizing a submitted review or new review comment) is implemented for GitHub — see [SKILLS.md](SKILLS.md). Everything else (plan/code/test/PR, review triage, and all of Lane 2) is still design, not code. Full design, state-derivation rules, and open questions: [CLAUDE.md](CLAUDE.md#target-architecture-two-lanes).

## Stack

- Django (project: `config`)
- LangChain for building/running agent chains (`agents/services.py`), with provider/model selectable per call (OpenAI, Anthropic, ...) via LangChain's `init_chat_model`
- LangSmith for tracing/observability of every chain run
- Django REST Framework for the API surface
- Linear's GraphQL API + webhooks (`linear` app) for Lane 1's ticket-side trigger
- GitHub webhooks (`github` app) for Lane 1's review-side trigger (listening only so far)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in the provider key(s) you'll use, LANGCHAIN_API_KEY, and (for Lane 1) the LINEAR_* and GITHUB_WEBHOOK_SECRET vars

python manage.py migrate
python manage.py createsuperuser   # optional, for /admin/
python manage.py runserver
```

To actually receive Linear webhooks locally, expose `localhost:8000` with a tunnel (e.g. `ngrok http 8000`) and register `<tunnel-url>/api/linear/webhook/` as an Issue webhook in Linear's settings, using the signing secret as `LINEAR_WEBHOOK_SECRET`.

Same idea for GitHub: register `<tunnel-url>/api/github/webhook/` as a webhook on the target repo (subscribed to "Pull request reviews" and "Pull request review comments"), using its signing secret as `GITHUB_WEBHOOK_SECRET`. Right now this only logs recognized review events — there's no triage or API client wired up yet.

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

# or pick a provider/model explicitly:
curl -X POST http://127.0.0.1:8000/api/agents/run/ \
  -H "Content-Type: application/json" \
  -d '{"prompt": "hello", "provider": "anthropic", "model": "claude-3-5-sonnet-latest"}'
```

Returns the model's response plus the LangSmith run id. Each call is also persisted as an `AgentRun` (prompt, response, provider, model, status, error, LangSmith run id) — visible in `/admin/`.

Lane 1's Linear trigger isn't something you curl directly — assign a Linear issue (in a project with the GitHub integration connected) to the bot user configured as `LINEAR_BOT_USER_ID`, and Linear's webhook delivery does the rest; the refined spec shows up as a comment on the issue.

## Layout

- `config/` — Django project settings, root URLs
- `agents/` — general LangChain plumbing: `models.py` (`AgentRun`), `services.py` (provider/model-selectable chain construction + invocation), `views.py` / `urls.py` (the `/api/agents/run/` endpoint)
- `linear/` — Lane 1's Linear-side trigger: `webhooks.py` (signature verification, event filtering), `client.py` (`LinearClient`), `services.py` (verify → read → refine), `views.py` / `urls.py` (the `/api/linear/webhook/` endpoint). No `models.py` — nothing here is persisted locally, by design (see [CLAUDE.md](CLAUDE.md#state-derived-not-stored)).
- `github/` — Lane 1's GitHub review-side trigger, listening only: `webhooks.py` (signature verification, event filtering), `services.py` (logs recognized review events — no triage yet), `views.py` / `urls.py` (the `/api/github/webhook/` endpoint). No `models.py`, no API client yet.

## Docs

- [HUMANS.md](HUMANS.md) — what this project is, in plain language, for non-technical readers
- [SKILLS.md](SKILLS.md) — catalog of what the pipeline can actually do today (and what's planned but not built yet)
- [CLAUDE.md](CLAUDE.md) — architecture notes and conventions for working in this repo with Claude Code
