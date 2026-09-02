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

### Linear ticket refine (Lane 1, steps 1–4)

Listens for a Linear issue being assigned to the bot, verifies (in plain code) the project's native GitHub integration is connected, then hands off to a tool-using agent bound to Linear's own MCP server: it reads the issue's full context (description, comments, linked issues) and posts the refined spec back as a comment itself. Stops (with an explanatory comment) instead of guessing if the GitHub integration isn't connected — that check stays deterministic code, not agent discretion, on purpose (see the implementation files below). Steps 5 onward (plan/code/test/PR/review) are not built — see "Planned" below.

- **Trigger**: Linear webhook → `POST /api/linear/webhook/` (configure this URL as an Issue webhook in Linear's settings)
- **Auth**: HMAC-SHA256 signature verification (`LINEAR_WEBHOOK_SECRET`) for the inbound webhook; `LINEAR_API_KEY` is sent as a bearer token to both the GraphQL API (`linear/client.py`) and Linear's MCP server (`linear/mcp.py`) — same credential, two transports
- **Filter**: only fires when the issue's assignee becomes `LINEAR_BOT_USER_ID` — see `linear/webhooks.py` (`is_issue_assigned_to`)
- **Implementation**: [linear/webhooks.py](linear/webhooks.py) (signature/event filtering), [linear/client.py](linear/client.py) (`LinearClient`, used only for the deterministic verify/fail-comment path), [linear/mcp.py](linear/mcp.py) (`build_linear_mcp_client`, wraps `langchain_mcp_adapters.MultiServerMCPClient` against `LINEAR_MCP_URL`), [linear/services.py](linear/services.py) (`handle_issue_assigned`, `verify_github_integration`, `refine_ticket_agent` — a `langgraph.prebuilt.create_react_agent` bound to Linear's live MCP tools) with its system prompt in [linear/prompts.py](linear/prompts.py) (`REFINE_AGENT_PROMPT`), exposed by [linear/views.py](linear/views.py) (`LinearWebhookView`)
- **Audit trail**: none locally, by design — the refined spec is the comment left on the Linear issue itself, and the whole agent run (LLM calls + tool calls) is traced in LangSmith. See [CLAUDE.md](CLAUDE.md#state-derived-not-stored).
- **Config**: `LINEAR_API_KEY`, `LINEAR_WEBHOOK_SECRET`, `LINEAR_BOT_USER_ID`, `LINEAR_MCP_URL` (defaults to `https://mcp.linear.app/mcp`), plus the LLM config above. Requires the Linear↔GitHub integration installed on the target team first — see [CLAUDE.md](CLAUDE.md#prerequisite-native-jiralinear--github-integration).
- **Runs inline** in the webhook request (no task queue yet) — an agent loop is slower and less bounded than the single LLM call this replaced, so this will need revisiting even sooner than before; see the "Long-running work" open decision.

### GitHub review ingestion (Lane 1, step 9 — listening only)

Listens for a GitHub pull request review being submitted, or a new inline review comment, and recognizes it as an event Lane 1 needs to act on. Does not yet triage the comment (fix vs. reply) or call the GitHub API at all — see "Planned" below.

- **Trigger**: GitHub webhook → `POST /api/github/webhook/` (configure this URL as a webhook on the target repo, subscribed to "Pull request reviews" and "Pull request review comments")
- **Auth**: HMAC-SHA256 signature verification (`GITHUB_WEBHOOK_SECRET`) against the `X-Hub-Signature-256` header — no payload timestamp/replay check the way Linear's webhook has, since GitHub's payload doesn't carry one
- **Filter**: only fires on a submitted review or a newly created review comment — see `github/webhooks.py` (`is_review_event`); GitHub's own `ping` event (sent when the webhook is first configured) is answered 200 without further processing
- **Implementation**: [github/webhooks.py](github/webhooks.py) (signature/event filtering), [github/services.py](github/services.py) (`handle_review_event` — currently just logs the event), exposed by [github/views.py](github/views.py) (`GitHubWebhookView`)
- **Audit trail**: none — the event is logged only; no persistence, no GitHub API call, per [CLAUDE.md](CLAUDE.md#state-derived-not-stored)
- **Config**: `GITHUB_WEBHOOK_SECRET`
- **Not yet built**: a GitHub API client (to read PR/CI/thread state or post replies), and the fix-vs-reply triage policy itself — see "Open questions" below

---

## Planned — Lane 1: Implementation agent (ticket → PR → merged)

Full flow described in [CLAUDE.md](CLAUDE.md#lane-1--implementation-agent-ticket--pr--merged). Steps 1–4 are implemented for Linear, and step 9's listening half is implemented for GitHub (see "Linear ticket refine" and "GitHub review ingestion" above); each item below becomes its own entry once built.

- Produce a dev plan from the refined ticket
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
