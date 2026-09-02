"""Webhook receiver for Linear issue events — Lane 1's trigger, step 1
(CLAUDE.md#lane-1--implementation-agent-ticket--pr--merged).

Runs the whole Lane 1 flow inline within the request, per the scaffolding
decision to defer a task queue until the flow itself is proven out (see
CLAUDE.md's "Long-running work" open decision).
"""

import json
import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from planner.workspace import CloneError

from .services import IntegrationNotConnected, handle_issue_assigned
from .webhooks import InvalidSignature, check_timestamp, is_issue_assigned_to, verify_signature

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class LinearWebhookView(View):
    """Signature verification is this endpoint's real authentication, so it's
    exempt from Django's session-oriented CSRF protection — a webhook has no
    session/cookie to carry a CSRF token in the first place.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        raw_body = request.body
        signature = request.headers.get('Linear-Signature', '')
        try:
            verify_signature(raw_body, signature, settings.LINEAR_WEBHOOK_SECRET)
        except InvalidSignature:
            return HttpResponse(status=401)

        try:
            payload = json.loads(raw_body)
        except ValueError:
            return HttpResponse(status=400)

        try:
            check_timestamp(payload.get('webhookTimestamp', 0))
        except InvalidSignature:
            return HttpResponse(status=401)

        if is_issue_assigned_to(payload, settings.LINEAR_BOT_USER_ID):
            try:
                handle_issue_assigned(payload['data']['id'])
            except (IntegrationNotConnected, CloneError):
                # Already commented on the ticket explaining why — that's
                # the intended terminal action for this event, not a bug.
                # A CloneError specifically won't be fixed by Linear
                # retrying the same webhook, so there's no point 500-ing.
                pass
            except Exception:
                logger.exception('Lane 1 failed for issue %s', payload['data'].get('id'))
                return HttpResponse(status=500)

        return HttpResponse(status=200)
