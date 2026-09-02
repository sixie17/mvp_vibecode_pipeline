"""Lane 1 step 5: produce a dev plan for a ticket against its target repo.

Clones the target repo read-only (see workspace.py for why cloning is
unavoidable), gives a tool-using agent read-only exploration tools scoped to
that clone (see tools.py), and returns the plan it produces. The clone is
always removed before this returns — see workspace.py's docstring for why
that doesn't conflict with this app's statelessness.

Not yet wired into linear/services.py: determining which GitHub repo/ref to
clone for a given Linear issue isn't solved yet (see IDEAS.md). This module
is deliberately decoupled from Linear so it's useful and testable on its
own without that being solved first.
"""

from langgraph.prebuilt import create_react_agent

from agents.services import build_chat_model

from .prompts import PLAN_AGENT_PROMPT
from .tools import build_repo_tools
from .workspace import cloned_repo


def plan_change(
    clone_url: str,
    ref: str,
    ticket_spec: str,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Clone `clone_url` at `ref`, explore it, and return a dev plan for `ticket_spec`."""
    with cloned_repo(clone_url, ref) as workdir:
        tools = build_repo_tools(workdir)
        llm = build_chat_model(provider, model)
        agent = create_react_agent(llm, tools, prompt=PLAN_AGENT_PROMPT)
        result = agent.invoke({
            'messages': [('user', f'Ticket spec:\n\n{ticket_spec}')],
        })
    return result['messages'][-1].content
