"""Lane 1 orchestration triggered by a Linear issue assignment.

Implements steps 2-5 of Lane 1 (CLAUDE.md#lane-1--implementation-agent-ticket--pr--merged):
verify the ticket's native GitHub integration is connected, hand off to a
tool-using agent that reads the ticket (via Linear's own MCP server) and
refines it into a concrete spec, then clone the target repo (planner/) and
produce a dev plan from that spec — posting both the spec and the plan back
as separate comments. Steps 6 onward (code, tests, PR, review triage) aren't
built yet.

Which GitHub repo to clone for planner.plan_change() isn't something Linear's
API can tell us — checked directly against Linear's public GraphQL schema,
there's no field on Issue/Team/Project exposing a team's connected GitHub
repo(s); the only place a repo appears is Issue.attachments, and only once a
branch/PR already exists, which is too late for planning. So this app holds
the target repo as config (TARGET_REPO_CLONE_URL/TARGET_REPO_DEFAULT_BRANCH)
rather than deriving it — one target repo per deployment for now, not a
per-team mapping. See IDEAS.md if that ever needs to become configurable
per-team.

Two things here deliberately stay plain deterministic code rather than
something the agent decides or does for itself:

- **The integration check** (verify_github_integration): CLAUDE.md's
  "Prerequisite" section is explicit that this must be verified live, not
  silently guessed past, and a code-level gate before the agent ever runs is
  the only way to guarantee that — an LLM instructed to check first can
  still be argued out of it or just get it wrong.
- **Posting the refined spec as a comment**: the agent's MCP connection is
  read-only (see linear/mcp.py) precisely so it has no write tool to call —
  it can only return the spec as text. This module is what actually calls
  LinearClient.create_comment() with that text, which guarantees exactly one
  comment gets posted with exactly the text the agent produced, rather than
  trusting the agent to have called a comment tool correctly (or at all).

Nothing here is persisted locally — every call re-reads the issue from
Linear, per CLAUDE.md#state-derived-not-stored. That includes idempotency:
Linear only waits 5 seconds for a webhook response before considering the
delivery failed and retrying (up to 3x, at 1min/1hr/6hr) — comfortably
shorter than two agent loops plus a git clone — so a single "assigned" event
reliably produces more than one call to handle_issue_assigned() for the same
issue. Rather than a local "have I already processed this" record, each step
here checks Linear's own comment history for its own marker prefix
(REFINE_COMMENT_PREFIX/PLAN_COMMENT_PREFIX) before redoing that step, so a
retried webhook becomes a no-op once the earlier attempt's work is visible
on the issue — see CLAUDE.md's "State: derived, not stored" -> Idempotency
paragraph and _find_existing_comment() below. A fail-comment (integration
not connected, clone failed) deliberately carries neither prefix, since
those are meant to be retried once the underlying problem is fixed, not
treated as "already done".
"""

import asyncio
from urllib.parse import quote, urlsplit, urlunsplit

from django.conf import settings
from langgraph.prebuilt import create_react_agent

from agents.services import build_chat_model
from planner.services import plan_change
from planner.workspace import CloneError

from .client import LinearClient
from .mcp import build_linear_mcp_client
from .prompts import REFINE_AGENT_PROMPT

REFINE_COMMENT_PREFIX = '**Refined spec:**\n\n'
PLAN_COMMENT_PREFIX = '**Dev plan:**\n\n'


class IntegrationNotConnected(Exception):
    """Raised when an issue has no usable branchName, meaning the native
    Linear<->GitHub integration isn't connected for this team/project — see
    CLAUDE.md's "Prerequisite: native Jira/Linear <-> GitHub integration".
    """


def verify_github_integration(issue: dict) -> None:
    """Confirm this issue can be linked to a GitHub branch/PR before starting work.

    branchName is present on every issue regardless of integration status,
    but it's meaningless without Linear's GitHub integration installed on
    the team — an empty value is the cheapest live signal that it isn't,
    without also requiring a separate GitHub API call.
    """
    if not issue.get('branchName'):
        raise IntegrationNotConnected(
            f"Issue {issue.get('identifier')} has no branchName; the "
            'Linear<->GitHub integration may not be connected for this team.'
        )


def _find_existing_comment(issue: dict, prefix: str) -> str | None:
    """Return the body (with `prefix` stripped) of an already-posted comment
    on `issue` starting with `prefix`, or None if there isn't one yet.

    This is the actual idempotency mechanism — see the module docstring —
    matched purely on comment body content, not comment authorship: Linear's
    Comment.user is null for some integration/bot auth configurations, so
    checking "who posted this" is less reliable than checking "does a
    comment with our marker already exist", and the prefixes here are
    distinctive enough that a false match from something else is not a
    realistic concern for what is an efficiency/idempotency check, not a
    security boundary.
    """
    for comment in (issue.get('comments') or {}).get('nodes', []):
        body = comment.get('body') or ''
        if body.startswith(prefix):
            return body[len(prefix):]
    return None


def _authenticated_clone_url(url: str, token: str) -> str:
    """Inject `token` as an HTTPS credential into `url` for cloning a private
    target repo — GitHub's own "x-access-token" pattern for using a PAT or
    App installation token with plain `git clone`
    (`https://x-access-token:<token>@github.com/owner/repo.git`), chosen
    over an SSH deploy key so cloning needs no new infrastructure (no key
    file, no ssh-agent, no known_hosts) beyond a URL string — see CLAUDE.md's
    "Prerequisite" section.

    Returns `url` unchanged if `token` is empty (a public repo needs none).
    Raises ValueError if a token is given but `url` isn't https — silently
    dropping the token would look like a working, but unauthenticated, config.
    """
    if not token:
        return url
    parts = urlsplit(url)
    if parts.scheme != 'https':
        raise ValueError(f'TARGET_REPO_ACCESS_TOKEN is set but TARGET_REPO_CLONE_URL is not an https:// URL: {url!r}')
    netloc = f'x-access-token:{quote(token, safe="")}@{parts.hostname}'
    if parts.port:
        netloc += f':{parts.port}'
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def _run_refine_agent(issue_identifier: str, *, provider: str | None = None, model: str | None = None) -> str:
    """Build a fresh MCP-backed agent and run it once for one issue.

    Tools come from Linear's own read-only MCP server at call time (see
    linear/mcp.py) rather than anything hardcoded here — the agent
    discovers and chooses which Linear tool to use for reading on its own.
    Its final answer is the refined spec text itself, not a side effect —
    see the module docstring for why posting it is handled elsewhere.
    """
    mcp_client = build_linear_mcp_client()
    tools = await mcp_client.get_tools()
    llm = build_chat_model(provider, model)
    agent = create_react_agent(llm, tools, prompt=REFINE_AGENT_PROMPT)
    result = await agent.ainvoke({
        'messages': [('user', f'Refine and comment on Linear issue {issue_identifier}.')],
    })
    return result['messages'][-1].content


def refine_ticket_agent(issue: dict, *, provider: str | None = None, model: str | None = None) -> str:
    """Sync entry point for _run_refine_agent(). Returns the refined spec
    text — this does not post anything, see handle_issue_assigned().

    asyncio.run() is safe here because Django's webhook view is a plain sync
    View with no event loop already running in this thread — see the
    "Long-running work" open decision in CLAUDE.md for why this whole
    request-inline approach (agent loop included) will need revisiting
    regardless, once steps 5-8 make a single webhook request take even
    longer than an LLM+tool-call loop already does.
    """
    return asyncio.run(_run_refine_agent(issue['identifier'], provider=provider, model=model))


def handle_issue_assigned(issue_id: str) -> str:
    """Lane 1 steps 2-5 for one issue: verify, refine (+ comment), plan (+ comment).

    If the GitHub integration isn't connected, comments that on the ticket
    and re-raises rather than guessing a branch/PR link or coding unlinked —
    see the module docstring for why this check isn't part of the agent.
    Once the agent returns its refined spec, this posts it via
    LinearClient.create_comment() — deterministic code, not an agent tool
    call — then clones TARGET_REPO_CLONE_URL (with TARGET_REPO_ACCESS_TOKEN
    injected as an HTTPS credential if the repo is private — see
    _authenticated_clone_url()) at its default branch (no ticket-specific
    branch exists yet — that's step 6) to produce a dev plan from that spec,
    posted as a second comment. Returns the plan text.

    Both the refine and plan steps are skipped (not redone) if a comment with
    their marker prefix already exists on the issue — see the module
    docstring and _find_existing_comment() for why this matters: Linear will
    reliably retry this webhook while this handler is still slower than its
    5-second timeout, and without this check each retry reran everything
    from scratch, repeatedly rewriting the same ticket's spec.

    A clone failure (bad branch, empty repo with no commits yet, no access)
    gets the same treatment as a missing GitHub integration: comment
    explaining what went wrong and re-raise, rather than an opaque 500 that
    Linear just keeps retrying forever — retrying won't fix a repo with no
    commits on it. CloneError's own message is already scrubbed of any
    embedded credential (see planner/workspace.py), so it's safe to include
    verbatim in a ticket comment. Neither fail-comment carries a marker
    prefix, so a later retry (after the human fixes the actual problem) is
    still free to proceed rather than being mistaken for "already done".
    """
    client = LinearClient(settings.LINEAR_API_KEY)
    issue = client.get_issue(issue_id)

    try:
        verify_github_integration(issue)
    except IntegrationNotConnected:
        client.create_comment(
            issue['id'],
            "I can't start on this ticket yet: this project's Linear<->GitHub "
            "integration doesn't look connected, so I have no way to link a "
            'branch or pull request back to it. Please connect the integration '
            'and reassign me.',
        )
        raise

    refined = _find_existing_comment(issue, REFINE_COMMENT_PREFIX)
    if refined is None:
        refined = refine_ticket_agent(issue)
        client.create_comment(issue['id'], REFINE_COMMENT_PREFIX + refined)

    existing_plan = _find_existing_comment(issue, PLAN_COMMENT_PREFIX)
    if existing_plan is not None:
        return existing_plan

    clone_url = _authenticated_clone_url(settings.TARGET_REPO_CLONE_URL, settings.TARGET_REPO_ACCESS_TOKEN)
    try:
        plan = plan_change(clone_url, settings.TARGET_REPO_DEFAULT_BRANCH, refined)
    except CloneError as exc:
        client.create_comment(
            issue['id'],
            "I refined this ticket, but couldn't clone the target repo to plan "
            f'against it: {exc}\n\nCheck TARGET_REPO_CLONE_URL/'
            'TARGET_REPO_DEFAULT_BRANCH, and that the repo has at least one '
            'commit on that branch, then reassign me.',
        )
        raise

    client.create_comment(issue['id'], PLAN_COMMENT_PREFIX + plan)
    return plan
