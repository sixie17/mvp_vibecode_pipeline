"""Lane 1 orchestration triggered by a Linear issue assignment.

Implements steps 2-4 of Lane 1 (CLAUDE.md#lane-1--implementation-agent-ticket--pr--merged):
verify the ticket's native GitHub integration is connected, then hand off to
a tool-using agent that reads the ticket (via Linear's own MCP server) and
refines it into a concrete spec, posted back as a comment. Steps 5 onward
(plan, code, tests, PR, review triage) aren't built yet.

The integration check deliberately stays plain deterministic code, not
something the agent decides for itself: CLAUDE.md's "Prerequisite" section
is explicit that this must be verified live, not silently guessed past, and
a code-level gate before the agent ever runs is the only way to guarantee
that — an LLM instructed to check first can still be argued out of it or
just get it wrong. So this uses linear/client.py's plain GraphQL call (the
same LINEAR_API_KEY the MCP server also uses) rather than routing through
the agent, and only invokes the agent once the check has already passed.

Nothing here is persisted locally — every call re-reads the issue from
Linear, per CLAUDE.md#state-derived-not-stored.
"""

import asyncio

from django.conf import settings
from langgraph.prebuilt import create_react_agent

from agents.services import build_chat_model

from .client import LinearClient
from .mcp import build_linear_mcp_client
from .prompts import REFINE_AGENT_PROMPT


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


async def _run_refine_agent(issue_identifier: str, *, provider: str | None = None, model: str | None = None) -> str:
    """Build a fresh MCP-backed agent and run it once for one issue.

    Tools come from Linear's own MCP server at call time (see linear/mcp.py)
    rather than anything hardcoded here — the agent discovers and chooses
    which Linear tool to use for reading/writing on its own.
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
    """Sync entry point for _run_refine_agent().

    asyncio.run() is safe here because Django's webhook view is a plain sync
    View with no event loop already running in this thread — see the
    "Long-running work" open decision in CLAUDE.md for why this whole
    request-inline approach (agent loop included) will need revisiting
    regardless, once steps 5-8 make a single webhook request take even
    longer than an LLM+tool-call loop already does.
    """
    return asyncio.run(_run_refine_agent(issue['identifier'], provider=provider, model=model))


def handle_issue_assigned(issue_id: str) -> str:
    """Lane 1 steps 2-4 for one issue: verify, then hand off to the refine agent.

    If the GitHub integration isn't connected, comments that on the ticket
    and re-raises rather than guessing a branch/PR link or coding unlinked —
    see the module docstring for why this check isn't part of the agent.
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

    return refine_ticket_agent(issue)
