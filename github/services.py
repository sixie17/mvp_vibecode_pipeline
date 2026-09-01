"""Lane 1 step 9 ingestion: receiving GitHub review events.

Implements only the "listen" half of step 9
(CLAUDE.md#lane-1--implementation-agent-ticket--pr--merged) — recognizing
that a review or review comment came in on a PR. Triage (deciding whether a
given comment needs a code fix + push versus just a reply) is an open design
decision (CLAUDE.md#open-design-decisions) and deliberately not implemented
here, and there's no GitHub API client yet to act on a decision even if there
were one. This just logs enough to confirm the ingestion path works end to
end; a future triage step plugs in where handle_review_event() is called
from github/views.py.
"""

import logging

logger = logging.getLogger(__name__)


def handle_review_event(event_type: str, payload: dict) -> None:
    """Log a recognized review event. Stands in for triage until the
    fix-vs-reply policy is decided and a GitHub client exists to act on it —
    see the module docstring.
    """
    pr = payload.get('pull_request') or {}
    repo = payload.get('repository') or {}
    actor = (payload.get('review') or payload.get('comment') or {}).get('user') or {}
    logger.info(
        'GitHub %s from %s on %s#%s (%s)',
        event_type,
        actor.get('login'),
        repo.get('full_name'),
        pr.get('number'),
        pr.get('html_url'),
    )
