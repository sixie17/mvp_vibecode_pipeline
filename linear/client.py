"""Thin GraphQL client for the Linear API.

Wraps only what Lane 1 needs so far: reading an issue's full state-derivation
context (see CLAUDE.md#state-derived-not-stored) and posting a comment back.
Nothing here is cached or persisted — every call hits Linear directly, per
the app's statelessness design.

Field/mutation shapes below are taken from Linear's public GraphQL schema
(https://github.com/linear/linear, packages/sdk/src/schema.graphql).
"""

import requests

API_URL = 'https://api.linear.app/graphql'

_GET_ISSUE_QUERY = """
query($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    branchName
    url
    assignee { id }
    attachments(filter: { sourceType: { eq: "github" } }) {
      nodes { url title sourceType }
    }
    comments {
      nodes { body }
    }
  }
}
"""

_CREATE_COMMENT_MUTATION = """
mutation($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
  }
}
"""


class LinearAPIError(Exception):
    """Raised when Linear's GraphQL API responds with errors."""


class LinearClient:
    def __init__(self, api_key: str):
        self._api_key = api_key

    def _execute(self, query: str, variables: dict) -> dict:
        response = requests.post(
            API_URL,
            json={'query': query, 'variables': variables},
            headers={'Authorization': self._api_key, 'Content-Type': 'application/json'},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get('errors'):
            raise LinearAPIError(str(payload['errors']))
        return payload['data']

    def get_issue(self, issue_id: str) -> dict:
        """Fetch an issue plus its GitHub linkage and comments: description
        for the "refine" step, branchName/attachments for the "verify
        integration" step, and comments so handle_issue_assigned() can tell
        whether refine/plan already ran for this issue (see
        linear/services.py's idempotency handling) without storing anything
        locally.
        """
        return self._execute(_GET_ISSUE_QUERY, {'id': issue_id})['issue']

    def create_comment(self, issue_id: str, body: str) -> None:
        """Post a comment on an issue. `issue_id` may be a UUID or an
        identifier like 'ENG-123' — Linear accepts either.
        """
        self._execute(_CREATE_COMMENT_MUTATION, {'issueId': issue_id, 'body': body})
