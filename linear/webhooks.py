"""Linear webhook signature verification and event filtering.

Pure functions, kept separate from views.py so they're testable without
touching Django's request/response machinery. Verification follows
https://linear.app/developers/webhooks: HMAC-SHA256 of the raw request body,
hex-compared against the `Linear-Signature` header, plus a freshness check
on the payload's own `webhookTimestamp` to reject replayed requests.
"""

import hashlib
import hmac
import time


class InvalidSignature(Exception):
    """Raised when a webhook fails signature or timestamp verification."""


def verify_signature(raw_body: bytes, signature: str, secret: str) -> None:
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ''):
        raise InvalidSignature('signature mismatch')


def check_timestamp(webhook_timestamp_ms: int, *, now: float | None = None, max_age_seconds: int = 60) -> None:
    now = now if now is not None else time.time()
    age_seconds = now - (webhook_timestamp_ms / 1000)
    if abs(age_seconds) > max_age_seconds:
        raise InvalidSignature('webhook timestamp outside allowed window')


def is_issue_assigned_to(payload: dict, user_id: str) -> bool:
    """True if this webhook event represents an Issue *becoming* assigned to `user_id`.

    Only fires on the assignment transition itself (issue created already
    assigned to us, or an update whose `updatedFrom` shows the assignee
    changed) — not on every other update to an issue we're already working,
    since what happens next is always re-derived from Linear/GitHub state
    (see CLAUDE.md#state-derived-not-stored), not from webhook payload noise.
    """
    if payload.get('type') != 'Issue':
        return False
    data = payload.get('data') or {}
    if data.get('assigneeId') != user_id:
        return False
    action = payload.get('action')
    if action == 'create':
        return True
    if action == 'update':
        return 'assigneeId' in (payload.get('updatedFrom') or {})
    return False
