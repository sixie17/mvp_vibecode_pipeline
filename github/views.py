"""Webhook receiver for GitHub pull request review events — Lane 1 step 9's
trigger (CLAUDE.md#lane-1--implementation-agent-ticket--pr--merged).

Listening only: recognizes relevant review events and hands them to
services.handle_review_event(), which currently just logs them (see that
module's docstring for why triage isn't wired up yet).
"""

import json
import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .services import handle_review_event
from .webhooks import InvalidSignature, is_review_event, verify_signature

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class GitHubWebhookView(View):
    """Signature verification is this endpoint's real authentication, so it's
    exempt from Django's session-oriented CSRF protection — a webhook has no
    session/cookie to carry a CSRF token in the first place.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        raw_body = request.body
        signature = request.headers.get('X-Hub-Signature-256', '')
        try:
            verify_signature(raw_body, signature, settings.GITHUB_WEBHOOK_SECRET)
        except InvalidSignature:
            return HttpResponse(status=401)

        event_type = request.headers.get('X-GitHub-Event', '')
        if event_type == 'ping':
            # Sent once when the webhook is first configured, to confirm
            # connectivity — there's no review payload to act on.
            return HttpResponse(status=200)

        try:
            payload = json.loads(raw_body)
        except ValueError:
            return HttpResponse(status=400)

        if is_review_event(event_type, payload):
            try:
                handle_review_event(event_type, payload)
            except Exception:
                logger.exception('GitHub review ingestion failed for event %s', event_type)
                return HttpResponse(status=500)

        return HttpResponse(status=200)
