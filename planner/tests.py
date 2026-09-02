import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from .services import plan_change
from .tools import PathEscapesRoot, build_repo_tools
from .workspace import CloneError, cloned_repo


class _FakeToolCallingChatModel(FakeMessagesListChatModel):
    """FakeMessagesListChatModel with bind_tools() made a no-op.

    create_react_agent() calls bind_tools() whenever any tools are passed in
    (plan_change() always passes three) — the base fake model's bind_tools()
    raises NotImplementedError, so this override just returns self, since
    the fake model already ignores what tools are available and returns its
    canned responses regardless.
    """

    def bind_tools(self, tools, **kwargs):
        return self


def _run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _make_local_repo(tmp_path: Path, *, branch: str = 'main') -> Path:
    """Create a tiny real git repo on disk — no network involved — so
    cloned_repo()/plan_change() can be tested against a real `git clone`
    without hitting GitHub.
    """
    src = tmp_path / 'src'
    src.mkdir()
    _run('git', 'init', '-b', branch, cwd=src)
    _run('git', 'config', 'user.email', 'test@example.com', cwd=src)
    _run('git', 'config', 'user.name', 'Test', cwd=src)
    (src / 'app.py').write_text('def add(a, b):\n    return a + b\n')
    (src / 'README.md').write_text('# demo\n')
    _run('git', 'add', '.', cwd=src)
    _run('git', 'commit', '-m', 'initial commit', cwd=src)
    return src


class ClonedRepoTests(SimpleTestCase):
    def test_clones_a_real_local_repo_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_local_repo(Path(tmp))

            with cloned_repo(f'file://{src}', 'main') as workdir:
                self.assertTrue(workdir.exists())
                self.assertEqual((workdir / 'app.py').read_text(), 'def add(a, b):\n    return a + b\n')

            self.assertFalse(workdir.exists())  # removed on exit

    def test_cleans_up_even_when_body_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_local_repo(Path(tmp))
            captured = {}
            with self.assertRaises(ValueError):
                with cloned_repo(f'file://{src}', 'main') as workdir:
                    captured['workdir'] = workdir
                    raise ValueError('boom')
            self.assertFalse(captured['workdir'].exists())

    def test_bad_ref_raises_clone_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _make_local_repo(Path(tmp))
            with self.assertRaises(CloneError):
                with cloned_repo(f'file://{src}', 'no-such-branch'):
                    pass

    def test_empty_repo_with_no_commits_raises_clone_error(self):
        """A freshly created GitHub repo with no initial commit has no
        branches at all yet - `git clone --branch main` fails exactly like a
        bad ref does, not some special case.
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'empty'
            src.mkdir()
            _run('git', 'init', '-b', 'main', cwd=src)
            with self.assertRaises(CloneError):
                with cloned_repo(f'file://{src}', 'main'):
                    pass

    def test_credential_in_clone_url_is_redacted_from_error_message(self):
        """clone_url may carry an embedded x-access-token for a private repo
        (see linear/services.py's _authenticated_clone_url()) - it must never
        end up readable in a raised error, since callers may reasonably show
        this message to a human (e.g. a ticket comment).
        """
        fake_failure = subprocess.CompletedProcess(
            args=['git', 'clone'],
            returncode=128,
            stdout='',
            stderr=(
                "fatal: unable to access "
                "'https://x-access-token:ghp_supersecret@github.com/acme/widgets.git/': "
                'The requested URL returned error: 403'
            ),
        )
        with patch('planner.workspace.subprocess.run', return_value=fake_failure):
            with self.assertRaises(CloneError) as ctx:
                with cloned_repo('https://x-access-token:ghp_supersecret@github.com/acme/widgets.git', 'main'):
                    pass

        self.assertNotIn('ghp_supersecret', str(ctx.exception))
        self.assertIn('https://***@github.com/acme/widgets.git', str(ctx.exception))


class RepoToolsTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'app.py').write_text('def add(a, b):\n    return a + b\n')
        (self.root / 'sub').mkdir()
        (self.root / 'sub' / 'util.py').write_text('def helper():\n    pass\n')
        self.list_files, self.read_file, self.grep = build_repo_tools(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_files_finds_everything_by_default(self):
        files = self.list_files.invoke({})
        self.assertIn('app.py', files)
        self.assertIn('sub/util.py', files)

    def test_list_files_respects_glob(self):
        files = self.list_files.invoke({'glob_pattern': 'sub/*.py'})
        self.assertEqual(files, ['sub/util.py'])

    def test_read_file_returns_contents(self):
        self.assertEqual(self.read_file.invoke({'path': 'app.py'}), 'def add(a, b):\n    return a + b\n')

    def test_read_file_missing_file_reports_it_without_raising(self):
        self.assertIn('No such file', self.read_file.invoke({'path': 'nope.py'}))

    def test_read_file_blocks_path_traversal(self):
        result = self.read_file.invoke({'path': '../outside.txt'})
        self.assertIn('resolves outside', result)

    def test_grep_finds_matching_lines(self):
        matches = self.grep.invoke({'pattern': 'def helper'})
        self.assertEqual(len(matches), 1)
        self.assertIn('sub/util.py:1:', matches[0])

    def test_grep_no_matches_returns_empty_list(self):
        self.assertEqual(self.grep.invoke({'pattern': 'nonexistent_symbol'}), [])

    def test_resolve_within_raises_for_absolute_escape(self):
        with self.assertRaises(PathEscapesRoot):
            from .tools import _resolve_within
            _resolve_within(self.root, '/etc/passwd')


class PlanChangeTests(SimpleTestCase):
    def test_returns_agent_final_message_against_a_real_local_clone(self):
        """Wiring smoke test: a real git clone (local, no network) plus a
        stubbed chat model, so this proves cloned_repo() + build_repo_tools()
        + create_react_agent() + cleanup all line up together — not that a
        real LLM produces a good plan.
        """
        fake_model = _FakeToolCallingChatModel(responses=[AIMessage(content='the dev plan')])

        with tempfile.TemporaryDirectory() as tmp:
            src = _make_local_repo(Path(tmp))
            with patch('planner.services.build_chat_model', return_value=fake_model):
                result = plan_change(f'file://{src}', 'main', 'Add a subtract() function.')

        self.assertEqual(result, 'the dev plan')
