"""GitHub webhook signature verification and event filtering.

Pure functions, kept separate from views.py so they're testable without
touching Django's request/response machinery. Verification follows
https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries:
HMAC-SHA256 of the raw request body, hex-compared (after stripping GitHub's
`sha256=` prefix) against the `X-Hub-Signature-256` header. Unlike Linear,
GitHub's payload carries no timestamp to check for replay, so there's no
equivalent of linear/webhooks.py's check_timestamp() here.
"""

import hashlib
import hmac

_SIGNATURE_PREFIX = 'sha256='


class InvalidSignature(Exception):
    """Raised when a webhook fails signature verification."""


def verify_signature(raw_body: bytes, signature_header: str, secret: str) -> None:
    signature_header = signature_header or ''
    if not signature_header.startswith(_SIGNATURE_PREFIX):
        raise InvalidSignature('missing or malformed X-Hub-Signature-256 header')
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header[len(_SIGNATURE_PREFIX):]
    if not hmac.compare_digest(expected, provided):
        raise InvalidSignature('signature mismatch')


_RELEVANT_ACTIONS = {
    'pull_request_review': {'submitted'},
    'pull_request_review_comment': {'created'},
}


def is_review_event(event_type: str, payload: dict) -> bool:
    """True for the two events that carry a human review verdict or comment
    Lane 1 step 9 needs to triage: a submitted review (approve/request
    changes/comment) or a new inline review comment. Everything else GitHub
    can send to this same endpoint (pushes, check runs, the initial `ping`,
    review edits/dismissals, ...) is filtered out here so downstream triage
    logic only ever sees events it actually needs to act on.
    """
    allowed_actions = _RELEVANT_ACTIONS.get(event_type)
    if not allowed_actions:
        return False
    return payload.get('action') in allowed_actions
