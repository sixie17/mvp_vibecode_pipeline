# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Django app that is the base for an autonomous coding pipeline built from two lanes that close a loop with each other — Lane 1 turns a ticket into a reviewed, merged PR; Lane 2 turns observed bad behavior in a deployed system into a ticket for Lane 1 to pick up. The app itself is designed to be **stateless**: no app-owned database or persistent storage for business state, ever — see "State: derived, not stored" below. See "Target architecture: two lanes" for the full design, and [SKILLS.md](SKILLS.md) for what's implemented vs still planned. Right now only the scaffolding exists — one Django app (`agents`) that runs a single LangChain chain through a REST endpoint, traced end-to-end in LangSmith. Neither lane is built yet, and that scaffold predates the statelessness decision (see the note in "Architecture" below).

## Commands

```bash
# setup (once)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in OPENAI_API_KEY and LANGCHAIN_API_KEY

# day to day
python manage.py runserver          # dev server
python manage.py migrate            # apply migrations
python manage.py makemigrations agents   # after changing agents/models.py
python manage.py check              # system check (fast sanity check, no DB needed)
python manage.py test agents        # run this app's tests
python manage.py test agents.tests.SomeTestCase.test_method  # single test
python manage.py createsuperuser    # for /admin/
```

There is no separate lint/format command configured yet.

## Architecture

- `config/` — the Django project (settings, root URLconf, WSGI/ASGI). `config/settings.py` calls `load_dotenv(BASE_DIR / '.env')` at import time, so every setting that comes from the environment (Django secret key/debug/hosts, `OPENAI_API_KEY`, `DEFAULT_LLM_MODEL`, and all `LANGCHAIN_*` vars) is available as soon as Django boots.
- `agents/` — the only app so far. Each future agent capability (Jira/Linear ingestion, PR creation, review, issue creation, ...) is expected to live as its own service module here or as its own app, following the same pattern:
  - `models.py` — `AgentRun`: one row per chain invocation (prompt, response, status, error, `langsmith_run_id`), persisted to the local DB. **This predates the statelessness decision below and is not the pattern to extend.** It's fine as-is for the existing "Run prompt" skill, but Lane 1/Lane 2 work must not add new DB-backed audit models — LangSmith is the trace history, and Linear/Jira/GitHub are the record of what happened (see "State: derived, not stored").
  - `services.py` — where LangChain chains are built and invoked. `build_chain()` wires a prompt template through `ChatOpenAI` to a string output parser; `run_prompt()` invokes it inside `collect_runs()` so the LangSmith run id can be captured. New agent capabilities should follow this shape — a `build_*` function that constructs the chain/graph, a `run_*` function that invokes it and returns a typed result — but for Lane 1/Lane 2 work, persist nothing locally; read whatever context is needed from Linear/Jira/GitHub at call time instead of from a DB row.
  - `views.py` / `urls.py` — thin DRF `APIView`s that call into `services.py`. `config/urls.py` mounts each app's urls under `/api/<app>/`.

### LangSmith observability

Tracing requires no per-call code — it's controlled entirely by environment variables read by LangChain/LangSmith directly from `os.environ` (`LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, `LANGCHAIN_ENDPOINT`; see `.env.example`). Any chain built with LangChain primitives is traced automatically once these are set. The only reason `services.py` uses `collect_runs()` is to grab the resulting run id so it can be stored on the local `AgentRun` row — that part is a convenience for cross-referencing, not what enables tracing.

## Target architecture: two lanes

This is the plan the current scaffolding is meant to grow into. Nothing below is implemented yet — treat it as the design to build toward, not a description of existing code. Both lanes share the LangChain/LangSmith foundation already in `agents/services.py` (build a chain/graph, invoke it, get tracing for free) — minus the DB persistence that module currently does, which does not carry forward (see "State: derived, not stored").

### Lane 1 — Implementation agent: ticket → PR → merged

Event-driven, one run per ticket:

1. **Listen** for a ticket assignment event from Jira or Linear (the agent is the assignee).
2. **Verify** the ticket tool's native GitHub integration is actually connected for this ticket (see "Prerequisite" below) — stop and comment on the ticket if not, rather than guessing.
3. **Read** the ticket (description, comments, linked issues/context).
4. **Refine** it — resolve ambiguity, expand it into a concrete, actionable spec. This step may write back to the ticket (clarifying comment) rather than silently guessing.
5. **Plan** — produce a dev plan (files to touch, approach, risks) before writing code.
6. **Code** the change.
7. **Write tests** covering it.
8. **Open a PR** referencing the ticket.
9. **Wait for review.** On a GitHub review event, triage each comment: some need a code fix + push, others just need a reply (question, pushback, already addressed elsewhere) — this is a per-comment decision, not one branch for the whole review.
10. Loop back to step 9 until the PR is approved/merged.

Each of steps 1–10 is a natural seam for a separate service module (or its own Django app) — see "Documentation conventions" below for how to register a new one in SKILLS.md as it's built.

### Lane 2 — Detection agent: telemetry/logs → ticket

Runs alongside a system this pipeline has been integrated into (i.e., *consuming* another project's observability, not this repo's own):

1. **Listen** to the target system's OpenTelemetry data or logs.
2. **Detect** unwanted behavior (errors, anomalies, regressions — the exact detection strategy is still open).
3. **Create a ticket** in Jira/Linear describing what was observed, with enough context (trace/log excerpts, timing, affected component) for Lane 1's "refine" step to act on without re-deriving it from raw telemetry.

### How the lanes connect

Lane 2's output (a new ticket) is Lane 1's trigger (a ticket assignment). The loop is: bad behavior observed → ticket filed → agent picks it up → PR → review → merge → (ideally) the bad behavior stops, which Lane 2 would also be positioned to confirm, though closing that verification loop is not yet designed.

### State: derived, not stored

The app holds no database and no persistent storage for business state — full stop. Nothing about "where a ticket is in the pipeline" is ever written to a table this app owns. Every webhook handler is a pure function: read current state from Linear/Jira and GitHub, decide the next action, act, exit. Nothing is cached or remembered between invocations.

This works because Linear/Jira and GitHub already carry everything needed — **not via a convention this app invents, but via the ticket tool's own native GitHub integration** (Linear↔GitHub, or the GitHub for Jira app):

- Linear exposes a suggested `branchName` on every issue via its API; the agent creates the branch under that exact name. GitHub PRs opened from it are then auto-linked back to the issue by Linear's integration — no parsing on this app's part.
- Jira's GitHub for Jira app does the equivalent (branch creation from the issue's Development panel, and a dev-status API reporting linked branches/PRs/commits for an issue).
- To answer "does this ticket already have a PR?" or "which ticket does this PR belong to?", the agent asks the ticket tool's own API for its linked dev info — it does not pattern-match branch names or PR bodies itself.

From that linked info, the state read collapses to:

- **No linked PR yet** → this ticket hasn't been started; begin at refine/plan.
- **Linked PR exists, has unresolved review threads** → triage those specific threads (fix vs. reply), nothing else.
- **Linked PR approved + CI green** → nothing to do, waiting on a human to merge.
- **Linked PR merged** → this ticket's Lane 1 work is done.

Identity (bot account, human user, or a bare API key) is likewise just injected credentials — the app never stores or reasons about *which* identity it's running as beyond using it to call the APIs.

**Lane 2 dedup** follows the same rule: before filing a ticket, search Linear/Jira for an already-open ticket carrying a matching fingerprint/label, instead of checking a local table.

**Idempotency**: because there's no local "have I already processed this event" record, every handler must re-derive state from the source of truth before acting — a replayed/duplicate webhook then naturally becomes a no-op (e.g. "PR already exists for this ticket" → skip straight to triage instead of re-coding) rather than needing its own dedup table.

### Prerequisite: native Jira/Linear ↔ GitHub integration

Because state derivation depends on it, this pipeline **requires** the ticket tool's native GitHub integration to already be installed and connected on both sides (the Linear↔GitHub app, or GitHub for Jira) before Lane 1 can run against a given project/repo. This is stated as a hard setup prerequisite — but a doc alone can't catch a lapsed or never-configured connection, so Lane 1 also verifies it live, per ticket, before doing any work: read the ticket's linked-dev info (e.g. Linear's `branchName`/attachment data, Jira's dev-status response) and if it comes back empty/erroring, stop and comment on the ticket explaining the integration isn't connected, rather than guessing a branch name or silently proceeding unlinked. This check is a plain read at request time — nothing is cached or stored, so it doesn't conflict with statelessness — it's re-checked on every run, not once at startup.

### Open design decisions

These are unresolved on purpose — don't silently pick one while implementing a lane; surface the choice.

*(Resolved: the ticket↔PR linking convention — settled on the ticket tool's native GitHub integration rather than a custom scheme; see "State: derived, not stored" and "Prerequisite" above.)*

- **Event ingestion**: webhooks (Jira/Linear/GitHub all support them) vs. polling. Webhooks imply this Django app needs a publicly reachable endpoint and signature verification; polling implies a scheduler.
- **Long-running work**: ticket-to-PR is a multi-minute, multi-step job. It needs *some* way to run outside a single request/response cycle (e.g. an async task queue, or a long-lived process per webhook), but per the statelessness rule above, whatever runs it must not become a second system of record — any queue/broker state is transient plumbing only, not something the app reads back state from. Not yet chosen.
- **"Assigned to them" identity**: how the bot's Jira/Linear/GitHub identity is configured and matched on incoming events.
- **Review comment triage**: the rule for "push a fix" vs "reply only" in step 8 of Lane 1 needs a concrete policy, not just "depends on the case."
- **Jira vs Linear**: both are named in the vision; whether both are supported from day one or one first is undecided.

## Documentation conventions

- Every module, service, model, and view in this repo carries a short docstring explaining *why* it exists or *what non-obvious thing* it does — not a restatement of the code. See `agents/services.py` and `agents/models.py` for the expected tone/length.
- When you add a new agent capability (a new chain, tool, or endpoint), add or update its entry in [SKILLS.md](SKILLS.md) — that file is the catalog of what the pipeline can actually do, kept separate from this operating-instructions file.
- Keep README.md's "Try it" / setup instructions in sync with any new endpoints or env vars.
