import hashlib
import hmac
import time
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from planner.workspace import CloneError

from .services import (
    PLAN_COMMENT_PREFIX,
    REFINE_COMMENT_PREFIX,
    IntegrationNotConnected,
    _authenticated_clone_url,
    _find_existing_comment,
    handle_issue_assigned,
    refine_ticket_agent,
    verify_github_integration,
)
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


class FindExistingCommentTests(SimpleTestCase):
    def test_no_comments_key_returns_none(self):
        self.assertIsNone(_find_existing_comment({'id': 'issue-1'}, REFINE_COMMENT_PREFIX))

    def test_no_matching_comment_returns_none(self):
        issue = {'comments': {'nodes': [{'body': 'just a human comment'}]}}
        self.assertIsNone(_find_existing_comment(issue, REFINE_COMMENT_PREFIX))

    def test_matching_comment_returns_body_with_prefix_stripped(self):
        issue = {'comments': {'nodes': [{'body': REFINE_COMMENT_PREFIX + 'the spec text'}]}}
        self.assertEqual(_find_existing_comment(issue, REFINE_COMMENT_PREFIX), 'the spec text')

    def test_different_prefix_does_not_match(self):
        issue = {'comments': {'nodes': [{'body': REFINE_COMMENT_PREFIX + 'the spec text'}]}}
        self.assertIsNone(_find_existing_comment(issue, PLAN_COMMENT_PREFIX))


class AuthenticatedCloneUrlTests(SimpleTestCase):
    def test_no_token_returns_url_unchanged(self):
        url = 'https://github.com/acme/widgets.git'
        self.assertEqual(_authenticated_clone_url(url, ''), url)

    def test_token_is_injected_as_https_credential(self):
        result = _authenticated_clone_url('https://github.com/acme/widgets.git', 'ghp_abc123')
        self.assertEqual(result, 'https://x-access-token:ghp_abc123@github.com/acme/widgets.git')

    def test_token_is_url_encoded(self):
        result = _authenticated_clone_url('https://github.com/acme/widgets.git', 'tok en/with?special')
        self.assertEqual(result, 'https://x-access-token:tok%20en%2Fwith%3Fspecial@github.com/acme/widgets.git')

    def test_non_https_url_with_token_raises(self):
        with self.assertRaises(ValueError):
            _authenticated_clone_url('git@github.com:acme/widgets.git', 'ghp_abc123')

    def test_non_https_url_without_token_is_fine(self):
        url = 'git@github.com:acme/widgets.git'
        self.assertEqual(_authenticated_clone_url(url, ''), url)


class RefineTicketAgentTests(SimpleTestCase):
    def test_returns_agent_final_message_with_no_live_mcp_or_llm_call(self):
        """Wiring smoke test: stubs both the MCP client and the chat model so
        this proves asyncio.run() + create_react_agent() + our message
        extraction all line up, without needing a real LINEAR_API_KEY or LLM
        credentials. It says nothing about what a real agent run would
        actually do against Linear's live MCP server.
        """
        fake_model = FakeMessagesListChatModel(responses=[AIMessage(content='refined spec text')])
        fake_mcp_client = AsyncMock()
        fake_mcp_client.get_tools.return_value = []

        with patch('linear.services.build_chat_model', return_value=fake_model), \
                patch('linear.services.build_linear_mcp_client', return_value=fake_mcp_client):
            result = refine_ticket_agent({'identifier': 'ENG-1'})

        self.assertEqual(result, 'refined spec text')


class HandleIssueAssignedTests(SimpleTestCase):
    def test_posts_refine_spec_then_plan_as_separate_comments_when_integration_connected(self):
        """The agent's read-only MCP tools mean it can't post the comment
        itself (see linear/mcp.py) — handle_issue_assigned() has to be the
        one that calls create_comment() with whatever the agent returned,
        then again with the plan produced from that spec.
        """
        issue = {'id': 'issue-1', 'identifier': 'ENG-1', 'branchName': 'user/eng-1-fix'}
        mock_client = MagicMock()
        mock_client.get_issue.return_value = issue

        with patch('linear.services.LinearClient', return_value=mock_client), \
                patch('linear.services.refine_ticket_agent', return_value='the refined spec') as mock_agent, \
                patch('linear.services.plan_change', return_value='the dev plan') as mock_plan, \
                patch('linear.services.settings') as mock_settings:
            mock_settings.LINEAR_API_KEY = 'key'
            mock_settings.TARGET_REPO_CLONE_URL = 'https://github.com/acme/widgets.git'
            mock_settings.TARGET_REPO_DEFAULT_BRANCH = 'main'
            mock_settings.TARGET_REPO_ACCESS_TOKEN = ''
            result = handle_issue_assigned('issue-1')

        mock_agent.assert_called_once_with(issue)
        mock_plan.assert_called_once_with('https://github.com/acme/widgets.git', 'main', 'the refined spec')
        self.assertEqual(
            mock_client.create_comment.call_args_list,
            [
                (('issue-1', REFINE_COMMENT_PREFIX + 'the refined spec'), {}),
                (('issue-1', PLAN_COMMENT_PREFIX + 'the dev plan'), {}),
            ],
        )
        self.assertEqual(result, 'the dev plan')

    def test_skips_refine_agent_when_refine_comment_already_exists(self):
        """Reproduces the reported bug: Linear retries a webhook it didn't
        get a fast enough response to (5s timeout - comfortably shorter than
        two agent loops plus a git clone), and without this check each retry
        reran refine from scratch and reposted a new spec.
        """
        issue = {
            'id': 'issue-1',
            'identifier': 'ENG-1',
            'branchName': 'user/eng-1-fix',
            'comments': {'nodes': [{'body': REFINE_COMMENT_PREFIX + 'already refined spec'}]},
        }
        mock_client = MagicMock()
        mock_client.get_issue.return_value = issue

        with patch('linear.services.LinearClient', return_value=mock_client), \
                patch('linear.services.refine_ticket_agent') as mock_agent, \
                patch('linear.services.plan_change', return_value='the dev plan') as mock_plan, \
                patch('linear.services.settings') as mock_settings:
            mock_settings.LINEAR_API_KEY = 'key'
            mock_settings.TARGET_REPO_CLONE_URL = 'https://github.com/acme/widgets.git'
            mock_settings.TARGET_REPO_DEFAULT_BRANCH = 'main'
            mock_settings.TARGET_REPO_ACCESS_TOKEN = ''
            handle_issue_assigned('issue-1')

        mock_agent.assert_not_called()
        mock_plan.assert_called_once_with('https://github.com/acme/widgets.git', 'main', 'already refined spec')
        # Only the plan comment gets (re-)posted - refine already had its comment.
        mock_client.create_comment.assert_called_once_with('issue-1', PLAN_COMMENT_PREFIX + 'the dev plan')

    def test_skips_everything_when_plan_comment_already_exists(self):
        issue = {
            'id': 'issue-1',
            'identifier': 'ENG-1',
            'branchName': 'user/eng-1-fix',
            'comments': {'nodes': [
                {'body': REFINE_COMMENT_PREFIX + 'already refined spec'},
                {'body': PLAN_COMMENT_PREFIX + 'already planned'},
            ]},
        }
        mock_client = MagicMock()
        mock_client.get_issue.return_value = issue

        with patch('linear.services.LinearClient', return_value=mock_client), \
                patch('linear.services.refine_ticket_agent') as mock_agent, \
                patch('linear.services.plan_change') as mock_plan:
            result = handle_issue_assigned('issue-1')

        mock_agent.assert_not_called()
        mock_plan.assert_not_called()
        mock_client.create_comment.assert_not_called()
        self.assertEqual(result, 'already planned')

    def test_posts_explanatory_comment_and_reraises_when_clone_fails(self):
        """A CloneError (bad branch, empty repo, no access) gets the same
        treatment as a missing GitHub integration: comment explaining why,
        then re-raise so the view knows not to 500/let Linear retry — see
        linear/views.py.
        """
        issue = {'id': 'issue-1', 'identifier': 'ENG-1', 'branchName': 'user/eng-1-fix'}
        mock_client = MagicMock()
        mock_client.get_issue.return_value = issue

        with patch('linear.services.LinearClient', return_value=mock_client), \
                patch('linear.services.refine_ticket_agent', return_value='the refined spec'), \
                patch('linear.services.plan_change', side_effect=CloneError('branch main not found')), \
                patch('linear.services.settings') as mock_settings:
            mock_settings.LINEAR_API_KEY = 'key'
            mock_settings.TARGET_REPO_CLONE_URL = 'https://github.com/acme/widgets.git'
            mock_settings.TARGET_REPO_DEFAULT_BRANCH = 'main'
            mock_settings.TARGET_REPO_ACCESS_TOKEN = ''
            with self.assertRaises(CloneError):
                handle_issue_assigned('issue-1')

        self.assertEqual(mock_client.create_comment.call_count, 2)
        failure_issue_id, failure_message = mock_client.create_comment.call_args_list[1].args
        self.assertEqual(failure_issue_id, 'issue-1')
        self.assertIn('branch main not found', failure_message)

    def test_stops_and_comments_without_running_agent_when_integration_missing(self):
        issue = {'id': 'issue-1', 'identifier': 'ENG-1', 'branchName': ''}
        mock_client = MagicMock()
        mock_client.get_issue.return_value = issue

        with patch('linear.services.LinearClient', return_value=mock_client), \
                patch('linear.services.refine_ticket_agent') as mock_agent:
            with self.assertRaises(IntegrationNotConnected):
                handle_issue_assigned('issue-1')

        mock_agent.assert_not_called()
        mock_client.create_comment.assert_called_once()
        posted_issue_id, posted_message = mock_client.create_comment.call_args.args
        self.assertEqual(posted_issue_id, 'issue-1')
        self.assertIn('integration', posted_message)
