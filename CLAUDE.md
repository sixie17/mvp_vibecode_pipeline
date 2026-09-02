# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Django app that is the base for an autonomous coding pipeline built from two lanes that close a loop with each other — Lane 1 turns a ticket into a reviewed, merged PR; Lane 2 turns observed bad behavior in a deployed system into a ticket for Lane 1 to pick up. Django is this control plane's own implementation language — it is not a constraint on what the pipeline can act on. The product goal is codebase-agnostic: speed up the scrum process and let humans manage tasks and review code, while the agent works against whatever target repo/stack a ticket points at (not just Django/Python). Steps that touch a target repo (Lane 1's plan/code/test/PR) must detect and parametrize that repo's own language and tooling rather than assuming this control plane's stack. The app itself is designed to be **stateless**: no app-owned database or persistent storage for business state, ever — see "State: derived, not stored" below. See "Target architecture: two lanes" for the full design, and [SKILLS.md](SKILLS.md) for what's implemented vs still planned. Four apps exist so far: `agents` (a general LangChain chain-running scaffold, provider/model selectable, traced in LangSmith), `linear` (Lane 1's ticket-side trigger — steps 1–5: listen, verify the GitHub integration, read, refine, plan), `github` (Lane 1 step 9's review-side trigger — listening only: recognizes a submitted review or new review comment on a PR; triaging it is not built), and `planner` (Lane 1 step 5's implementation — clones a target repo and produces a dev plan; called from `linear/services.py`, not exposed via its own endpoint). Lane 2 and the rest of Lane 1 (code/test/PR/review triage) are still design only.

## Commands

```bash
# setup (once)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in the provider API key(s) you'll use and LANGCHAIN_API_KEY

# day to day
python manage.py runserver          # dev server
python manage.py migrate            # apply migrations
python manage.py makemigrations agents   # after changing agents/models.py (agents is the only app with models)
python manage.py check              # system check (fast sanity check, no DB needed)
python manage.py test agents linear github planner # run all four apps' tests
python manage.py test linear.tests.VerifySignatureTests.test_valid_signature_passes  # single test
python manage.py createsuperuser    # for /admin/
```

There is no separate lint/format command configured yet.

## Architecture

- `config/` — the Django project (settings, root URLconf, WSGI/ASGI). `config/settings.py` calls `load_dotenv(BASE_DIR / '.env')` at import time, so every setting that comes from the environment (Django secret key/debug/hosts, `DEFAULT_LLM_PROVIDER`/`DEFAULT_LLM_MODEL`, `LINEAR_*`, and all `LANGCHAIN_*` vars) is available as soon as Django boots. Provider API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, ...) are deliberately *not* read into a setting — each provider's LangChain integration reads its own key straight from the environment.
- `agents/` — general LangChain plumbing, reused by every capability in the repo:
  - `services.py` — `build_chat_model(provider, model)` builds a chat model via LangChain's `init_chat_model`, falling back to `DEFAULT_LLM_PROVIDER`/`DEFAULT_LLM_MODEL` when either is omitted. `build_chain()`/`run_prompt()` wrap that into the "Run prompt" skill's chain, invoked inside `collect_runs()` so the LangSmith run id can be captured. Any new chain (in `agents/` or elsewhere, e.g. `linear/services.py`) should call `build_chat_model()` rather than constructing a provider client directly, so provider/model selection stays consistent everywhere.
  - `prompts.py` — `ChatPromptTemplate`s used by this app's chains, kept out of `services.py` so prompt text can be read/edited independently of chain-building logic. Each app owns its own `prompts.py` (see `linear/prompts.py`) rather than sharing one module across apps.
  - `models.py` — `AgentRun`: one row per "Run prompt" invocation (prompt, response, provider, model, status, error, `langsmith_run_id`), persisted to the local DB. **This predates the statelessness decision below and is not the pattern to extend.** It's fine as-is for this one skill, but Lane 1/Lane 2 work must not add new DB-backed audit models — LangSmith is the trace history, and Linear/Jira/GitHub are the record of what happened (see "State: derived, not stored"). Note `linear/` below has no `models.py` at all, on purpose.
  - `views.py` / `urls.py` — thin DRF `APIView`s that call into `services.py`. `config/urls.py` mounts each app's urls under `/api/<app>/`.
- `linear/` — Lane 1's Linear-side trigger, steps 1–4 only (see "Lane 1" below). No `models.py` — nothing here is persisted, by design.
  - `webhooks.py` — pure functions: `verify_signature()`/`check_timestamp()` implement Linear's HMAC-SHA256 webhook verification, `is_issue_assigned_to()` filters payloads down to genuine assignment-change events. Kept dependency-free (no Django imports) so they're trivially unit-testable — see `linear/tests.py`.
  - `client.py` — `LinearClient`: a thin GraphQL wrapper (`get_issue`, `create_comment`) used only for the deterministic parts of the flow now — see `services.py` below for why those two calls stayed GraphQL instead of moving to MCP with everything else.
  - `mcp.py` — `build_linear_mcp_client()`: builds a `langchain_mcp_adapters.MultiServerMCPClient` pointed at Linear's own hosted **read-only** MCP server (`LINEAR_MCP_URL`, default `https://mcp.linear.app/mcp/readonly`), bearer-authenticated with `LINEAR_API_KEY`. Built fresh per call, not held open — same statelessness reasoning as everywhere else. Deliberately doesn't hardcode any tool names; discovering/choosing tools is left to the agent. Read-only is deliberate, not incidental — see `services.py` below.
  - `services.py` — `handle_issue_assigned(issue_id)`: fetches the issue via `LinearClient` and runs `verify_github_integration()` — a plain deterministic check, kept out of the agent on purpose — commenting and re-raising `IntegrationNotConnected` if it fails. Once that passes, `refine_ticket_agent()` hands off to a `langgraph.prebuilt.create_react_agent()` bound to Linear's live **read-only** MCP tool list (via `mcp.py`), which reads the issue's full context (description, comments, linked issues) and returns the refined spec as its answer — it cannot post the comment itself, since no write tool is bound to it. `handle_issue_assigned()` is what actually calls `LinearClient.create_comment()` with that returned text, then calls `planner.services.plan_change()` (cloning `TARGET_REPO_CLONE_URL` at `TARGET_REPO_DEFAULT_BRANCH` — not the issue's own `branchName`, since no ticket-specific branch exists remotely yet) to produce a dev plan from the refined spec, posted as a second comment. For a private target repo, `_authenticated_clone_url()` injects `TARGET_REPO_ACCESS_TOKEN` into that URL as an HTTPS credential (`https://x-access-token:<token>@...`, GitHub's own pattern for a PAT/App installation token) before handing it to `plan_change()` — chosen over an SSH deploy key so this needs no new infrastructure (no key file, no ssh-agent, no known_hosts), just a URL string; raises `ValueError` rather than silently dropping the token if `TARGET_REPO_CLONE_URL` isn't `https://`. A `CloneError` from `plan_change()` (bad branch, an empty repo with no commits yet, no access) gets the same treatment as a missing GitHub integration — comment explaining what went wrong, re-raise — since retrying the same webhook won't fix a repo that has no commits on it; `linear/views.py` catches `CloneError` alongside `IntegrationNotConnected` so Linear isn't told to retry either. Both the refine and plan steps are skipped, not redone, if a comment with their marker prefix (`REFINE_COMMENT_PREFIX`/`PLAN_COMMENT_PREFIX`) already exists on the issue (`_find_existing_comment()`) — see "Idempotency" below for why this matters in practice, not just in theory. **Why the integration check and comment-posting stay code, not agent discretion:** the integration check must be verified live per CLAUDE.md's "Prerequisite" section, never silently guessed past — a code-level gate run *before* the agent exists is the only way to guarantee that. Posting through a read-only-bound agent would mean trusting it to call a write tool correctly (or at all) — routing it through code instead guarantees exactly one comment with exactly the returned text, and means a prompt-injected ticket can't talk the agent into writing something else, since it has no write capability to invoke regardless of what it's told. Step 6 onward (code/test/PR) will be added next.
  - `prompts.py` — `REFINE_AGENT_PROMPT`: the agent's system prompt (a plain string, not a `ChatPromptTemplate`, since `create_react_agent()` takes a system message directly), kept separate from orchestration logic for the same reason as `agents/prompts.py`.
  - `views.py` — `LinearWebhookView`: `csrf_exempt` (the HMAC signature is the real auth), runs the whole flow inline in the request per the "Long-running work" decision below, returns 401 on bad signature/stale timestamp, 500 (so Linear retries) on an unexpected error, 200 otherwise — including when `IntegrationNotConnected` or `CloneError` (from `planner/`) was raised, since commenting on the ticket *is* the intended handling of both cases, not a failure to retry.
- `github/` — Lane 1's review-side trigger (step 9), listening only so far — no `models.py`, no API client, nothing here is persisted or written back to GitHub yet.
  - `webhooks.py` — pure functions: `verify_signature()` implements GitHub's HMAC-SHA256 webhook verification (`X-Hub-Signature-256`, prefixed `sha256=`, no timestamp/replay check the way Linear has), `is_review_event()` filters payloads down to a submitted review or a newly created review comment. Dependency-free like `linear/webhooks.py`, for the same testability reason — see `github/tests.py`.
  - `services.py` — `handle_review_event(event_type, payload)`: currently just logs the event (repo, PR, reviewer). This is a placeholder for step 9's triage logic, which isn't built — see "Open design decisions" below and the module's docstring for why.
  - `views.py` — `GitHubWebhookView`: `csrf_exempt` for the same reason as `LinearWebhookView`, short-circuits GitHub's `ping` event (sent when the webhook is first configured), returns 401 on bad signature, 400 on unparseable JSON, 500 on an unexpected error while handling a recognized event, 200 otherwise.
- `planner/` — Lane 1 step 5's implementation, called from `linear/services.py`. No `models.py`, no views/urls of its own — it's called as a plain function, not triggered by its own endpoint.
  - `workspace.py` — `cloned_repo(clone_url, ref)`: a context manager that shallow-clones a target repo into a fresh temp directory and always removes it on exit. This is scratch space for one call, not persisted state — see the module docstring and IDEAS.md's "codebase context and execution environment" section for why cloning is unavoidable here and why it doesn't conflict with statelessness. Auth isn't handled here on purpose: `clone_url` must already carry any credential a private repo needs — `planner/` stays GitHub-agnostic, it's `linear/services.py`'s `_authenticated_clone_url()` that actually builds an authenticated URL (see above). A failed clone raises `CloneError`, whose message is scrubbed of any embedded `scheme://credential@` substring (git's own stderr can echo a failing URL — token and all — back on an auth/connection failure, not just what this module interpolates itself) since callers may reasonably surface this message somewhere a human can see it, like a ticket comment.
  - `tools.py` — `build_repo_tools(root)`: builds `list_files`/`read_file`/`grep` as LangChain tools scoped to one cloned root via closures, with path-traversal protection (`_resolve_within`) so a tool call can't read outside the clone.
  - `prompts.py` — `PLAN_AGENT_PROMPT`, the planning agent's system prompt.
  - `services.py` — `plan_change(clone_url, ref, ticket_spec)`: clones the repo, binds the read-only tools to a `langgraph.prebuilt.create_react_agent`, and returns the dev plan it produces. Independently testable on its own (see `planner/tests.py`) and doesn't know anything about Linear — `linear/services.py` is what supplies `clone_url`/`ref` from config (`TARGET_REPO_CLONE_URL`/`TARGET_REPO_DEFAULT_BRANCH`), since Linear's API can't tell us which GitHub repo an issue belongs to (see "Prerequisite" below and IDEAS.md for why that had to become config instead of something derived).

### LangSmith observability

Tracing requires no per-call code — it's controlled entirely by environment variables read by LangChain/LangSmith directly from `os.environ` (`LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, `LANGCHAIN_ENDPOINT`; see `.env.example`). Any chain built with LangChain primitives is traced automatically once these are set. The only reason `services.py` uses `collect_runs()` is to grab the resulting run id so it can be stored on the local `AgentRun` row — that part is a convenience for cross-referencing, not what enables tracing.

## Target architecture: two lanes

This is the plan the current scaffolding is meant to grow into. Lane 1 steps 1–4 are implemented for Linear (see `linear/` above and [SKILLS.md](SKILLS.md)); everything else below — the rest of Lane 1, and all of Lane 2 — is still design, not code. Both lanes share the LangChain/LangSmith foundation in `agents/services.py` (build a chain/graph, invoke it, get tracing for free) — minus the DB persistence that module does for its own "Run prompt" skill, which does not carry forward (see "State: derived, not stored").

### Lane 1 — Implementation agent: ticket → PR → merged

Event-driven, one run per ticket. Steps 1–5 are implemented for Linear (`linear/views.py` + `linear/services.py`, step 5 via `planner/`); 6 onward are not built:

1. **Listen** for a ticket assignment event from Jira or Linear (the agent is the assignee). *(Implemented: Linear webhook → `LinearWebhookView`.)*
2. **Verify** the ticket tool's native GitHub integration is actually connected for this ticket (see "Prerequisite" below) — stop and comment on the ticket if not, rather than guessing. *(Implemented: `verify_github_integration`.)*
3. **Read** the ticket (description, comments, linked issues/context). *(Implemented: the refine agent below reads this itself via Linear's MCP tools — description, comments, and linked issues.)*
4. **Refine** it — resolve ambiguity, expand it into a concrete, actionable spec. This step may write back to the ticket (clarifying comment) rather than silently guessing. *(Implemented as a tool-using agent (`refine_ticket_agent`, bound to Linear's read-only MCP server) that reads the ticket and returns the refined spec; `handle_issue_assigned()` posts it as a comment — see `linear/services.py`. No back-and-forth clarification loop yet — see "Open design decisions" for what that needs.)*
5. **Plan** — produce a dev plan (files to touch, approach, risks) before writing code. *(Implemented: `handle_issue_assigned` clones the target repo (`planner.services.plan_change`) at its default branch and produces a plan from the refined spec, posted as a second comment — see `planner/` above.)*
6. **Code** the change.
7. **Write tests** covering it.
8. **Open a PR** referencing the ticket.
9. **Wait for review.** On a GitHub review event, triage each comment: some need a code fix + push, others just need a reply (question, pushback, already addressed elsewhere) — this is a per-comment decision, not one branch for the whole review. *(Listening only is implemented: GitHub webhook → `GitHubWebhookView`, filtered to submitted reviews and new review comments. Triage itself is not built — see "Open design decisions".)*
10. Loop back to step 9 until the PR is approved/merged.

Steps 5–8 act on the *target* repo the ticket belongs to, which is not necessarily this repo and not necessarily Django/Python — this control plane must detect that repo's own language, structure, and test/build tooling rather than assuming its own stack.

Each of steps 1–10 is a natural seam for a separate service module (or its own Django app) — see "Documentation conventions" below for how to register a new one in SKILLS.md as it's built.

### Lane 2 — Detection agent: telemetry/logs → ticket

Runs alongside a system this pipeline has been integrated into (i.e., *consuming* another project's observability, not this repo's own):

1. **Listen** to the target system's OpenTelemetry data or logs.
2. **Detect** unwanted behavior (errors, anomalies, regressions — the exact detection strategy is still open, see [IDEAS.md](IDEAS.md)).
3. **Create a ticket** in Jira/Linear describing what was observed, with enough context (trace/log excerpts, timing, affected component) for Lane 1's "refine" step to act on without re-deriving it from raw telemetry. **Filed unassigned, not auto-assigned to the bot** — auto-assigning it would immediately trigger Lane 1's "assigned to bot" webhook with no human ever having looked at whether the detection was worth acting on, which is exactly the kind of unnecessary bot-triggers-bot loop this pipeline should avoid. A human reviews the filed ticket and assigns it to the bot when they actually want Lane 1 to pick it up.

### How the lanes connect

Lane 2's output is a ticket, filed unassigned (see step 3 above); Lane 1's trigger is a human assigning that ticket to the bot. The loop is: bad behavior observed → ticket filed → a human decides it's worth acting on → agent picks it up → PR → review → merge → (ideally) the bad behavior stops, which Lane 2 would also be positioned to confirm, though closing that verification loop is not yet designed.

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

**Idempotency**: because there's no local "have I already processed this event" record, every handler must re-derive state from the source of truth before acting — a replayed/duplicate webhook then naturally becomes a no-op (e.g. "PR already exists for this ticket" → skip straight to triage instead of re-coding) rather than needing its own dedup table. This isn't theoretical: Linear's webhook delivery times out at 5 seconds and retries up to 3x (1min/1hr/6hr) on timeout or a non-200 — comfortably shorter than `linear/services.py`'s refine-agent-plus-clone-plus-plan-agent flow, so a single "assigned" event reliably produces more than one call to `handle_issue_assigned()`. It re-derives "have I already done this step" by checking Linear's own comment history for a marker prefix (`REFINE_COMMENT_PREFIX`/`PLAN_COMMENT_PREFIX`) before redoing that step, rather than skipping blindly — see `linear/services.py`'s `_find_existing_comment()`. This covers *success* — a *persistent failure* (same precondition fails on every retry) isn't deduped the same way and can post its explanatory comment more than once; see "Open design decisions" -> "Failure comments aren't deduped".

### Prerequisite: native Jira/Linear ↔ GitHub integration

Because state derivation depends on it, this pipeline **requires** the ticket tool's native GitHub integration to already be installed and connected on both sides (the Linear↔GitHub app, or GitHub for Jira) before Lane 1 can run against a given project/repo. This is stated as a hard setup prerequisite — but a doc alone can't catch a lapsed or never-configured connection, so Lane 1 also verifies it live, per ticket, before doing any work: read the ticket's linked-dev info (e.g. Linear's `branchName`/attachment data, Jira's dev-status response) and if it comes back empty/erroring, stop and comment on the ticket explaining the integration isn't connected, rather than guessing a branch name or silently proceeding unlinked. This check is a plain read at request time — nothing is cached or stored, so it doesn't conflict with statelessness — it's re-checked on every run, not once at startup.

**This linked-dev info tells you *whether* an issue is linked, not *which* GitHub repo it's linked to** — confirmed directly against Linear's public GraphQL schema: there's no field on `Issue`, `Team`, or `Project` exposing a connected repo, only `Issue.attachments` once a branch/PR already exists (too late for step 5's planning, which needs the repo before any branch exists). So `TARGET_REPO_CLONE_URL`/`TARGET_REPO_DEFAULT_BRANCH` (see `config/settings.py`) are held as config, not derived — one target repo per deployment for now. This is config (like `LINEAR_API_KEY`), not the pipeline state the statelessness rule is about, since there's no Linear-side source of truth for it to drift out of sync with. If the target repo is private, `TARGET_REPO_ACCESS_TOKEN` (a fine-grained PAT or GitHub App installation token, scoped to just that repo) is also config for the same reason — injected into the clone URL as an HTTPS credential at call time (`linear/services.py`'s `_authenticated_clone_url()`) rather than an SSH deploy key, since that needs no key-file/ssh-agent/known_hosts infrastructure this app doesn't otherwise have.

### Open design decisions

These are unresolved on purpose — don't silently pick one while implementing a lane; surface the choice.

*(Resolved: the ticket↔PR linking convention — settled on the ticket tool's native GitHub integration rather than a custom scheme; see "State: derived, not stored" and "Prerequisite" above.)*

*(Resolved for Linear, scaffolding only: event ingestion is webhooks — `linear/views.py`, signature-verified via `LINEAR_WEBHOOK_SECRET` — and the flow runs inline in the request rather than on a task queue. Both choices should be revisited once Lane 1 actually does the slow parts (plan/code/test/PR): inline execution will start timing out requests, and webhooks need this app to be publicly reachable, which isn't addressed yet either.)*

- **Long-running work, for real**: now that step 5 runs an agent loop *and* a `git clone` inline in the webhook request, this is even less likely to hold up than before — needs an async task queue (e.g. Celery) or similar, without that queue's state becoming a second system of record (see "State: derived, not stored"). Confirmed in practice, not just in theory: assigning a ticket doesn't feel instant — the refine agent's LLM+MCP tool round trips have to fully complete before the first comment appears at all, so there's real, user-visible latency (on top of Linear's separate 5s-timeout-triggers-a-retry problem covered under "Idempotency" below) between assignment and any visible sign of life. An async queue fixes both: return 200 immediately, do the work in the background.
- **Failure comments aren't deduped, only success comments are**: `_find_existing_comment()`'s idempotency check (see "Idempotency" below) only recognizes `REFINE_COMMENT_PREFIX`/`PLAN_COMMENT_PREFIX` — deliberately, so a fixable failure isn't mistaken for "already done" and skipped forever. But the flip side showed up in practice: a *persistent, unfixed* failure (e.g. `TARGET_REPO_DEFAULT_BRANCH` pointing at a branch the target repo doesn't have) gets its explanatory comment reposted on every one of Linear's automatic retries (up to 3x, at 1min/1hr/6hr — see "Idempotency"), since each retry fails the same way and nothing recognizes "I already explained this exact problem." Result: the same "couldn't clone the target repo..." comment can appear 2-3 times on one ticket before Linear gives up retrying. Not yet fixed — a real solution needs to distinguish "haven't tried" / "tried, told you why, waiting on a fix" / "already succeeded" rather than today's two-state (has a success marker, or doesn't).
- **Single-repo config vs. a per-team/per-issue mapping**: `TARGET_REPO_CLONE_URL`/`TARGET_REPO_DEFAULT_BRANCH` assume one deployment serves one target repo (see "Prerequisite" above for why this is config, not derived). If this pipeline ever needs to serve multiple Linear teams pointing at different repos from one deployment, that assumption breaks and needs a real team→repo mapping — deliberately not built now since no such need exists yet.
- **"Assigned to them" identity for Jira/GitHub**: Linear's is resolved (`LINEAR_BOT_USER_ID`, matched in `is_issue_assigned_to`); Jira and the reviewer identity used on GitHub still need the same treatment.
- **A clarification back-and-forth for step 4**: today's refine agent posts one spec and stops; if it instead needs to ask a question and wait for an answer, Lane 1 would need to listen on Linear's comment-created events too, not just issue-assignment. Two things any such design must get right: (1) **filter out comments authored by the bot itself** — the same self-loop risk `is_issue_assigned_to` already guards against for assignment, but for comments: without checking the comment author against `LINEAR_BOT_USER_ID`, the bot's own question would re-trigger its own handler, which could reply to itself indefinitely. (2) Statelessness still applies: don't persist "which question is this reply answering" — re-fetch the issue's full comment thread from Linear on every new (non-bot) comment and let the agent re-derive context from that, the same way ticket state is always re-derived rather than cached. Not yet designed or built.
- **Review comment triage**: the rule for "push a fix" vs "reply only" in step 9 of Lane 1 needs a concrete policy, not just "depends on the case."
- **Jira vs Linear**: Linear is built first (see above); whether/when Jira support gets added is undecided.
- **Codebase context for steps 5–8, and Lane 2's detection strategy**: both still speculative rather than blocking-and-undecided — see [IDEAS.md](IDEAS.md) rather than duplicating that reasoning here. Move an idea's outcome up into this list once it's actually decided.

## Documentation conventions

- Every module, service, model, and view in this repo carries a short docstring explaining *why* it exists or *what non-obvious thing* it does — not a restatement of the code. See `agents/services.py` and `agents/models.py` for the expected tone/length.
- When you add a new agent capability (a new chain, tool, or endpoint), add or update its entry in [SKILLS.md](SKILLS.md) — that file is the catalog of what the pipeline can actually do, kept separate from this operating-instructions file.
- Keep README.md's "Try it" / setup instructions in sync with any new endpoints or env vars.
