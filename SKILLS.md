# Skills

A catalog of the capabilities this pipeline actually implements today, kept up to date as agent capabilities are added. For *how* the code is organized, and the full design behind the two lanes below, see [CLAUDE.md](CLAUDE.md#target-architecture-two-lanes) and [README.md](README.md).

Each entry: what it does, how to invoke it, and where its trace/audit trail lives. Entries under "Planned" should move up to "Implemented" (with those details filled in) as each one is actually built — don't batch multiple lane steps into one entry.

---

## Implemented

### Run prompt

Sends a single prompt through a LangChain chain and returns the model's response.

- **Endpoint**: `POST /api/agents/run/`
- **Request**: `{"prompt": "<text>"}`
- **Response**: `{"id": <AgentRun id>, "response": "<text>", "langsmith_run_id": "<uuid>"}`
- **Implementation**: [agents/services.py](agents/services.py) (`build_chain`, `run_prompt`), exposed by [agents/views.py](agents/views.py) (`AgentRunView`)
- **Audit trail**: every call is persisted as an [agents/models.py](agents/models.py) `AgentRun` row (prompt, response, status, error) and traced in full in LangSmith under the `LANGCHAIN_PROJECT` project — the two are linked via `langsmith_run_id`, visible in `/admin/`. *(This DB-backed audit trail predates the app's statelessness decision — see [CLAUDE.md](CLAUDE.md#state-derived-not-stored) — and is not the pattern for new skills below: those read state from Linear/Jira/GitHub instead of persisting locally.)*
- **Config**: `OPENAI_API_KEY`, `DEFAULT_LLM_MODEL` (defaults to `gpt-4o-mini`)

---

## Planned — Lane 1: Implementation agent (ticket → PR → merged)

Full flow described in [CLAUDE.md](CLAUDE.md#lane-1--implementation-agent-ticket--pr--merged). Each step below becomes its own entry above once built, with its own endpoint/trigger and audit trail.

- Listen for a Jira/Linear ticket assigned to the agent
- Read the ticket (description, comments, linked context)
- Refine the ticket into a concrete spec, writing back a clarifying comment if needed
- Produce a dev plan from the refined ticket
- Implement the code change
- Write tests for the change
- Open a PR referencing the ticket
- Listen for GitHub review events on open PRs
- Triage each review comment: push a code fix, or reply only
- Push updates after a fix; loop until merged

## Planned — Lane 2: Detection agent (telemetry/logs → ticket)

Full flow described in [CLAUDE.md](CLAUDE.md#lane-2--detection-agent-telemetrylogs--ticket). Runs against a system this pipeline was integrated into, not against this repo itself.

- Listen to the target system's OpenTelemetry data / logs
- Detect unwanted behavior (anomaly/error/regression — detection strategy still open)
- Create a Jira/Linear ticket describing the observed behavior, with enough context for Lane 1's "refine" step to act on it

## Open questions

Tracked in full in [CLAUDE.md](CLAUDE.md#open-design-decisions): event ingestion (webhooks vs polling), how long-running lane-1 jobs are executed without becoming a second system of record, how the bot's identity is matched on incoming events, the review-comment triage policy, and whether Jira and Linear are both supported from the start.

(The ticket↔PR linking convention is resolved — see [CLAUDE.md](CLAUDE.md#state-derived-not-stored): rely on the ticket tool's native GitHub integration, not a custom scheme.)
