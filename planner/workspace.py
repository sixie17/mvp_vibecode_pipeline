"""Ephemeral, read-only working copies of a target repo for Lane 1's
code-touching steps (currently just step 5, plan).

Cloning is unavoidable for anything past raw ticket text: GitHub's API has
no way to run a shell command, and its Contents/Trees API is a slow,
rate-limited substitute for real grep — see IDEAS.md's "codebase context and
execution environment" section for the full reasoning. A clone made here is
scratch space for the duration of one call, not persisted state: nothing
about pipeline state is ever inferred from its existence, and it's always
deleted before cloned_repo()'s context manager exits, matching
CLAUDE.md#state-derived-not-stored.

Auth is deliberately not handled here — `clone_url` must already carry
whatever credential a private repo needs. planner/ stays GitHub-agnostic on
purpose: it only knows "clone this URL", not Linear or GitHub tokens. The
caller builds that URL — see linear/services.py's `_authenticated_clone_url()`
for how it injects `TARGET_REPO_ACCESS_TOKEN` as an embedded x-access-token
credential for a private target repo.

Because of that embedded credential, CloneError's message is scrubbed of any
`scheme://credential@` substring before being raised — git's own stderr can
echo the failing URL (token and all) back on an auth/connection failure, not
just the interpolation of `clone_url` this module does itself. Callers may
reasonably surface this message somewhere a human can see it (e.g. a ticket
comment), so it must never carry a live credential.
"""

import contextlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_CREDENTIAL_IN_URL_RE = re.compile(r'://[^/\s@]+@')


class CloneError(Exception):
    """Raised when `git clone` fails — bad URL, bad ref, or no access."""


@contextlib.contextmanager
def cloned_repo(clone_url: str, ref: str, *, timeout_seconds: int = 120):
    """Shallow-clone `clone_url` at `ref` into a fresh temp directory, yield
    its path, and always remove it on exit — success or failure.
    """
    workdir = Path(tempfile.mkdtemp(prefix='lane1-clone-'))
    try:
        result = subprocess.run(
            ['git', 'clone', '--depth', '1', '--branch', ref, clone_url, str(workdir)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0:
            message = f'git clone of {clone_url!r} at {ref!r} failed: {result.stderr.strip()}'
            raise CloneError(_CREDENTIAL_IN_URL_RE.sub('://***@', message))
        yield workdir
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
