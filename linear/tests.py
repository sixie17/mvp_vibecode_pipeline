import hashlib
import hmac
import time

from django.test import SimpleTestCase

from .services import IntegrationNotConnected, verify_github_integration
from .webhooks import InvalidSignature, check_timestamp, is_issue_assigned_to, verify_signature


class VerifySignatureTests(SimpleTestCase):
    def test_valid_signature_passes(self):
        secret = 'shh'
        body = b'{"hello":"world"}'
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        verify_signature(body, signature, secret)  # does not raise

    def test_wrong_signature_raises(self):
        with self.assertRaises(InvalidSignature):
            verify_signature(b'{"hello":"world"}', 'wrong', 'shh')

    def test_missing_signature_raises(self):
        with self.assertRaises(InvalidSignature):
            verify_signature(b'{}', '', 'shh')

    def test_signature_for_different_body_raises(self):
        secret = 'shh'
        signature = hmac.new(secret.encode(), b'{"a":1}', hashlib.sha256).hexdigest()
        with self.assertRaises(InvalidSignature):
            verify_signature(b'{"a":2}', signature, secret)


class CheckTimestampTests(SimpleTestCase):
    def test_recent_timestamp_passes(self):
        now = time.time()
        check_timestamp(int(now * 1000), now=now)  # does not raise

    def test_stale_timestamp_raises(self):
        now = time.time()
        stale_ms = int((now - 3600) * 1000)
        with self.assertRaises(InvalidSignature):
            check_timestamp(stale_ms, now=now)

    def test_future_timestamp_raises(self):
        now = time.time()
        future_ms = int((now + 3600) * 1000)
        with self.assertRaises(InvalidSignature):
            check_timestamp(future_ms, now=now)


class IsIssueAssignedToTests(SimpleTestCase):
    def test_created_already_assigned_to_bot(self):
        payload = {'type': 'Issue', 'action': 'create', 'data': {'assigneeId': 'bot-1'}}
        self.assertTrue(is_issue_assigned_to(payload, 'bot-1'))

    def test_created_assigned_to_someone_else(self):
        payload = {'type': 'Issue', 'action': 'create', 'data': {'assigneeId': 'human-1'}}
        self.assertFalse(is_issue_assigned_to(payload, 'bot-1'))

    def test_reassigned_to_bot(self):
        payload = {
            'type': 'Issue',
            'action': 'update',
            'data': {'assigneeId': 'bot-1'},
            'updatedFrom': {'assigneeId': 'human-1'},
        }
        self.assertTrue(is_issue_assigned_to(payload, 'bot-1'))

    def test_unrelated_update_on_already_assigned_issue_ignored(self):
        payload = {
            'type': 'Issue',
            'action': 'update',
            'data': {'assigneeId': 'bot-1'},
            'updatedFrom': {'title': 'old title'},
        }
        self.assertFalse(is_issue_assigned_to(payload, 'bot-1'))

    def test_non_issue_event_ignored(self):
        payload = {'type': 'Comment', 'action': 'create', 'data': {'assigneeId': 'bot-1'}}
        self.assertFalse(is_issue_assigned_to(payload, 'bot-1'))


class VerifyGithubIntegrationTests(SimpleTestCase):
    def test_issue_with_branch_name_passes(self):
        verify_github_integration({'identifier': 'ENG-1', 'branchName': 'user/eng-1-fix'})  # does not raise

    def test_issue_without_branch_name_raises(self):
        with self.assertRaises(IntegrationNotConnected):
            verify_github_integration({'identifier': 'ENG-1', 'branchName': ''})
