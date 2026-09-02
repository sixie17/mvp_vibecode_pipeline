"""Linear MCP client wiring for the ticket-refine agent (see
refine_ticket_agent() in services.py).

Deliberately points at Linear's **read-only** hosted MCP server
(https://linear.app/docs/mcp documents this as a separate URL from the
read-write one), not the read-write one. The refine agent's job is to
explore and produce spec text, never to write to Linear — see
services.py's handle_issue_assigned() for why posting the result back is
deterministic code instead of an agent-invoked tool call. Binding the agent
to a read-only connection makes that a structural guarantee (no write tool
exists for it to call, or be talked into calling by a prompt-injected
ticket) rather than something enforced only by the system prompt.

Authenticated with a bearer token — the same LINEAR_API_KEY
linear/client.py's GraphQL calls use, just presented as an MCP header
instead of a GraphQL Authorization header, per Linear's documented
server-to-server auth mode (no interactive OAuth needed).

A fresh client is built per call rather than held open, matching this app's
statelessness (see CLAUDE.md#state-derived-not-stored) — there's no
persistent connection for a once-per-webhook agent run to manage.

Deliberately no tool names or schemas are hardcoded here: which Linear tool
to call for what is left entirely to the agent built in services.py. That's
the actual point of using Linear's MCP server instead of hand-writing more
GraphQL queries in client.py — Linear owns and evolves that tool surface,
not this app.
"""

from django.conf import settings
from langchain_mcp_adapters.client import MultiServerMCPClient


def build_linear_mcp_client() -> MultiServerMCPClient:
    return MultiServerMCPClient({
        'linear': {
            'transport': 'streamable_http',
            'url': settings.LINEAR_MCP_URL,
            'headers': {'Authorization': f'Bearer {settings.LINEAR_API_KEY}'},
        }
    })
