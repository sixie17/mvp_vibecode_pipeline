# Skills

A catalog of the capabilities this pipeline actually implements today, kept up to date as agent capabilities are added. For *how* the code is organized, and the full design behind the two lanes below, see [CLAUDE.md](CLAUDE.md#target-architecture-two-lanes) and [README.md](README.md).

Each entry: what it does, how to invoke it, and where its trace/audit trail lives. Entries under "Planned" should move up to "Implemented" (with those details filled in) as each one is actually built — don't batch multiple lane steps into one entry.

---

## Implemented

### Run prompt

Sends a single prompt through a LangChain chain and returns the model's response. Provider/model are selectable per call.

- **Endpoint**: `POST /api/agents/run/`
- **Request**: `{"prompt": "<text>", "provider": "<optional, e.g. 'anthropic'>", "model": "<optional, e.g. 'claude-3-5-sonnet-latest'>"}` — omit `provider`/`model` to use the configured defaults.
- **Response**: `{"id": <AgentRun id>, "response": "<text>", "langsmith_run_id": "<uuid>"}`
- **Implementation**: [agents/services.py](agents/services.py) (`build_chat_model`, `build_chain`, `run_prompt`) with its prompt template in [agents/prompts.py](agents/prompts.py) (`RUN_PROMPT`), exposed by [agents/views.py](agents/views.py) (`AgentRunView`)
- **Audit trail**: every call is persisted as an [agents/models.py](agents/models.py) `AgentRun` row (prompt, response, provider, model, status, error) and traced in full in LangSmith under the `LANGCHAIN_PROJECT` project — the two are linked via `langsmith_run_id`, visible in `/admin/`. *(This DB-backed audit trail predates the app's statelessness decision — see [CLAUDE.md](CLAUDE.md#state-derived-not-stored) — and is not the pattern for new skills below: those read state from Linear/Jira/GitHub instead of persisting locally.)*
- **Config**: `DEFAULT_LLM_PROVIDER` (defaults to `openai`), `DEFAULT_LLM_MODEL` (defaults to `gpt-4o-mini`), plus whichever provider API key(s) you use (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, ...)

### Linear ticket refine + plan (Lane 1, steps 1–5)

Listens for a Linear issue being assigned to the bot, verifies (in plain code) the project's native GitHub integration is connected, then hands off to a tool-using agent bound to Linear's own **read-only** MCP server: it reads the issue's full context (description, comments, linked issues) and returns the refined spec as its answer. Posting that spec back as a comment is plain code, not an agent tool call — the agent has no write tool to call in the first place. From there, the same handler clones the target repo and runs a second tool-using agent to produce a dev plan from that spec, posted as a second comment. Stops (with an explanatory comment) instead of guessing if the GitHub integration isn't connected, or if cloning the target repo fails (bad branch, an empty repo with no commits yet, no access) — both cases comment and stop rather than crash or have Linear retry a webhook that would just fail identically every time. Idempotent by design: Linear's 5-second webhook timeout is comfortably shorter than this flow (two agent loops plus a git clone), so a single "assigned" event reliably triggers more than one call — each of the refine/plan steps checks the issue's own comments for its marker prefix first and skips redoing that step if it's already there, rather than reposting a duplicate spec/plan on every retry. Steps 6 onward (code/test/PR/review) are not built — see "Planned" below.

- **Trigger**: Linear webhook → `POST /api/linear/webhook/` (configure this URL as an Issue webhook in Linear's settings)
- **Auth**: HMAC-SHA256 signature verification (`LINEAR_WEBHOOK_SECRET`) for the inbound webhook; `LINEAR_API_KEY` is sent as a bearer token to both the GraphQL API (`linear/client.py`) and Linear's read-only MCP server (`linear/mcp.py`) — same credential, two transports
- **Filter**: only fires when the issue's assignee becomes `LINEAR_BOT_USER_ID` — see `linear/webhooks.py` (`is_issue_assigned_to`)
- **Implementation**: [linear/webhooks.py](linear/webhooks.py) (signature/event filtering), [linear/client.py](linear/client.py) (`LinearClient`, used for the deterministic verify/fail-comment path *and* for posting both the refine agent's and the plan agent's output), [linear/mcp.py](linear/mcp.py) (`build_linear_mcp_client`, wraps `langchain_mcp_adapters.MultiServerMCPClient` against `LINEAR_MCP_URL`, Linear's read-only endpoint), [linear/services.py](linear/services.py) (`handle_issue_assigned`, `verify_github_integration`, `refine_ticket_agent` — a `langgraph.prebuilt.create_react_agent` bound to Linear's live read-only MCP tools) with its system prompt in [linear/prompts.py](linear/prompts.py) (`REFINE_AGENT_PROMPT`), exposed by [linear/views.py](linear/views.py) (`LinearWebhookView`). Step 5 itself is [planner/](planner/) — see "Dev plan generation" below.
- **Audit trail**: none locally, by design — the refined spec and the dev plan are each a comment left on the Linear issue itself, and both agent runs (LLM calls + tool calls) are traced in LangSmith. See [CLAUDE.md](CLAUDE.md#state-derived-not-stored).
- **Config**: `LINEAR_API_KEY`, `LINEAR_WEBHOOK_SECRET`, `LINEAR_BOT_USER_ID`, `LINEAR_MCP_URL` (defaults to `https://mcp.linear.app/mcp/readonly`), `TARGET_REPO_CLONE_URL`, `TARGET_REPO_DEFAULT_BRANCH` (defaults to `main`), `TARGET_REPO_ACCESS_TOKEN` (optional, for a private target repo), plus the LLM config above. Requires the Linear↔GitHub integration installed on the target team first — see [CLAUDE.md](CLAUDE.md#prerequisite-native-jiralinear--github-integration). `TARGET_REPO_CLONE_URL` is separate config, not derived from Linear, because Linear's API has no field exposing which GitHub repo a team/issue is connected to — see [CLAUDE.md](CLAUDE.md#prerequisite-native-jiralinear--github-integration) and [IDEAS.md](IDEAS.md).
- **Runs inline** in the webhook request (no task queue yet) — two agent loops plus a `git clone` is slower and less bounded than the single LLM call this started as, so this will need revisiting sooner rather than later; see the "Long-running work" open decision.

### GitHub review ingestion (Lane 1, step 9 — listening only)

Listens for a GitHub pull request review being submitted, or a new inline review comment, and recognizes it as an event Lane 1 needs to act on. Does not yet triage the comment (fix vs. reply) or call the GitHub API at all — see "Planned" below.

- **Trigger**: GitHub webhook → `POST /api/github/webhook/` (configure this URL as a webhook on the target repo, subscribed to "Pull request reviews" and "Pull request review comments")
- **Auth**: HMAC-SHA256 signature verification (`GITHUB_WEBHOOK_SECRET`) against the `X-Hub-Signature-256` header — no payload timestamp/replay check the way Linear's webhook has, since GitHub's payload doesn't carry one
- **Filter**: only fires on a submitted review or a newly created review comment — see `github/webhooks.py` (`is_review_event`); GitHub's own `ping` event (sent when the webhook is first configured) is answered 200 without further processing
- **Implementation**: [github/webhooks.py](github/webhooks.py) (signature/event filtering), [github/services.py](github/services.py) (`handle_review_event` — currently just logs the event), exposed by [github/views.py](github/views.py) (`GitHubWebhookView`)
- **Audit trail**: none — the event is logged only; no persistence, no GitHub API call, per [CLAUDE.md](CLAUDE.md#state-derived-not-stored)
- **Config**: `GITHUB_WEBHOOK_SECRET`
- **Not yet built**: a GitHub API client (to read PR/CI/thread state or post replies), and the fix-vs-reply triage policy itself — see "Open questions" below

### Dev plan generation (Lane 1, step 5's implementation)

Given a target repo, a ref, and a ticket spec, clones the repo read-only and runs a tool-using agent that explores it (list files, read a file, grep for a pattern) before producing a concrete dev plan: which files need to change and how, the approach, and any risks/ambiguity. Called from `linear/services.py` (see "Linear ticket refine + plan" above) with `TARGET_REPO_CLONE_URL`/`TARGET_REPO_DEFAULT_BRANCH` and the refined spec; also independently callable/testable on its own since it knows nothing about Linear.

- **Trigger**: none of its own — called as `planner.services.plan_change(clone_url, ref, ticket_spec)`, currently only from `linear/services.py`'s `handle_issue_assigned`.
- **Implementation**: [planner/workspace.py](planner/workspace.py) (`cloned_repo` — shallow git clone into a temp dir, always removed on exit), [planner/tools.py](planner/tools.py) (`build_repo_tools` — `list_files`/`read_file`/`grep` as LangChain tools scoped to one clone, with path-traversal protection), [planner/services.py](planner/services.py) (`plan_change`, a `langgraph.prebuilt.create_react_agent` bound to those tools) with its system prompt in [planner/prompts.py](planner/prompts.py) (`PLAN_AGENT_PROMPT`)
- **Audit trail**: none — the plan is returned to the caller, which (today) posts it as a Linear comment; nothing planner itself persists
- **Config**: no settings of its own — reuses the LLM config above and takes `clone_url`/`ref` as plain arguments from its caller. `clone_url` must already carry any credential a private repo needs — see `linear/services.py`'s `_authenticated_clone_url()` for how the caller builds that from `TARGET_REPO_ACCESS_TOKEN`
- **Not yet built**: everything steps 6–8 will need (write access, running the target repo's own test suite, opening a PR) — see [IDEAS.md](IDEAS.md)

---

## Planned — Lane 1: Implementation agent (ticket → PR → merged)

Full flow described in [CLAUDE.md](CLAUDE.md#lane-1--implementation-agent-ticket--pr--merged). Steps 1–5 are implemented for Linear, and step 9's listening half is implemented for GitHub (see "Linear ticket refine + plan", "Dev plan generation", and "GitHub review ingestion" above); each item below becomes its own entry once built.

- Implement the code change
- Write tests for the change
- Open a PR referencing the ticket
- A GitHub API client to read PR/CI/review-thread state and post comments/commits (ingestion currently only listens — see "GitHub review ingestion" above)
- Triage each review comment: push a code fix, or reply only
- Push updates after a fix; loop until merged
- Support Jira as a second ticket source (Linear is first — see "Open questions")

## Planned — Lane 2: Detection agent (telemetry/logs → ticket)

Full flow described in [CLAUDE.md](CLAUDE.md#lane-2--detection-agent-telemetrylogs--ticket). Runs against a system this pipeline was integrated into, not against this repo itself.

- Listen to the target system's OpenTelemetry data / logs
- Detect unwanted behavior (anomaly/error/regression — detection strategy still open)
- Create a Jira/Linear ticket describing the observed behavior, with enough context for Lane 1's "refine" step to act on it

## Open questions

Tracked in full in [CLAUDE.md](CLAUDE.md#open-design-decisions): how long-running Lane 1 jobs (once plan/code/test/PR exist) get executed without becoming a second system of record, how the bot's identity is matched on incoming Jira/GitHub events, the review-comment triage policy, and when/whether Jira support gets added.

Resolved: the ticket↔PR linking convention (rely on the ticket tool's native GitHub integration, not a custom scheme) and, for Linear specifically, event ingestion (webhooks) and execution model (inline, for now) — see [CLAUDE.md](CLAUDE.md#state-derived-not-stored) and [CLAUDE.md](CLAUDE.md#open-design-decisions).
