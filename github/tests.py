import hashlib
import hmac

from django.test import SimpleTestCase

from .webhooks import InvalidSignature, is_review_event, verify_signature


class VerifySignatureTests(SimpleTestCase):
    def test_valid_signature_passes(self):
        secret = 'shh'
        body = b'{"hello":"world"}'
        signature = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        verify_signature(body, signature, secret)  # does not raise

    def test_wrong_signature_raises(self):
        with self.assertRaises(InvalidSignature):
            verify_signature(b'{"hello":"world"}', 'sha256=wrong', 'shh')

    def test_missing_signature_raises(self):
        with self.assertRaises(InvalidSignature):
            verify_signature(b'{}', '', 'shh')

    def test_missing_prefix_raises(self):
        secret = 'shh'
        body = b'{}'
        bare_hex = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        with self.assertRaises(InvalidSignature):
            verify_signature(body, bare_hex, secret)

    def test_signature_for_different_body_raises(self):
        secret = 'shh'
        signature = 'sha256=' + hmac.new(secret.encode(), b'{"a":1}', hashlib.sha256).hexdigest()
        with self.assertRaises(InvalidSignature):
            verify_signature(b'{"a":2}', signature, secret)


class IsReviewEventTests(SimpleTestCase):
    def test_submitted_review_is_relevant(self):
        self.assertTrue(is_review_event('pull_request_review', {'action': 'submitted'}))

    def test_dismissed_review_is_not_relevant(self):
        self.assertFalse(is_review_event('pull_request_review', {'action': 'dismissed'}))

    def test_created_review_comment_is_relevant(self):
        self.assertTrue(is_review_event('pull_request_review_comment', {'action': 'created'}))

    def test_edited_review_comment_is_not_relevant(self):
        self.assertFalse(is_review_event('pull_request_review_comment', {'action': 'edited'}))

    def test_unrelated_event_type_is_not_relevant(self):
        self.assertFalse(is_review_event('push', {'action': 'created'}))

    def test_ping_event_is_not_relevant(self):
        self.assertFalse(is_review_event('ping', {}))
