"""Lane 1 orchestration triggered by a Linear issue assignment.

Implements steps 2-4 of Lane 1 (CLAUDE.md#lane-1--implementation-agent-ticket--pr--merged):
verify the ticket's native GitHub integration is connected, read the ticket,
and refine it into a concrete spec, posted back as a comment. Steps 5 onward
(plan, code, tests, PR, review triage) aren't built yet.

Nothing here is persisted locally — every call re-reads the issue from
Linear, per CLAUDE.md#state-derived-not-stored.
"""

from django.conf import settings
from langchain_core.output_parsers import StrOutputParser

from agents.services import build_chat_model

from .client import LinearClient
from .prompts import REFINE_PROMPT


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


def refine_ticket(issue: dict, *, provider: str | None = None, model: str | None = None) -> str:
    """Expand a ticket's raw description into a concrete spec via the LLM.

    Reuses agents.services.build_chat_model so provider/model selection
    works the same way here as in the "Run prompt" skill.
    """
    llm = build_chat_model(provider, model)
    chain = REFINE_PROMPT | llm | StrOutputParser()
    return chain.invoke({
        'identifier': issue['identifier'],
        'title': issue['title'],
        'description': issue.get('description') or '(no description provided)',
    })


def handle_issue_assigned(issue_id: str) -> str:
    """Lane 1 steps 2-4 for one issue: verify, read, refine, comment back.

    If the GitHub integration isn't connected, comments that on the ticket
    and re-raises rather than guessing a branch/PR link or coding unlinked.
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

    refined = refine_ticket(issue)
    client.create_comment(issue['id'], refined)
    return refined
