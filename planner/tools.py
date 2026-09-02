"""Read-only LangChain tools for exploring one cloned repo.

Each tool is scoped to a single root directory via closures — built fresh
per clone (see build_repo_tools()) rather than taking a root as a parameter
the model could supply, since path-traversal safety depends on rejecting
paths that resolve outside that root, and that's simplest to guarantee with
the root fixed in the closure rather than trusted from model input.
"""

import re
from pathlib import Path

from langchain_core.tools import tool


class PathEscapesRoot(Exception):
    """Raised when a tool-requested path resolves outside the repo root."""


def _resolve_within(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathEscapesRoot(f'{relative_path!r} resolves outside the repo root')
    return candidate


def build_repo_tools(root: Path, *, max_file_bytes: int = 100_000, max_matches: int = 200):
    """Build list_files/read_file/grep tools scoped to `root`."""
    root = root.resolve()

    @tool
    def list_files(glob_pattern: str = '**/*') -> list[str]:
        """List files in the repo matching a glob pattern (default: every file)."""
        return sorted(str(p.relative_to(root)) for p in root.glob(glob_pattern) if p.is_file())

    @tool
    def read_file(path: str) -> str:
        """Read one file's contents, given a path relative to the repo root."""
        try:
            target = _resolve_within(root, path)
        except PathEscapesRoot as exc:
            return str(exc)
        if not target.is_file():
            return f'No such file: {path}'
        data = target.read_bytes()[:max_file_bytes]
        return data.decode('utf-8', errors='replace')

    @tool
    def grep(pattern: str, glob_pattern: str = '**/*') -> list[str]:
        """Search file contents for a regex pattern across files matching a
        glob. Returns up to 200 'path:line: text' matches.
        """
        regex = re.compile(pattern)
        matches = []
        for file_path in root.glob(glob_pattern):
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(f'{file_path.relative_to(root)}:{line_number}: {line.strip()}')
                    if len(matches) >= max_matches:
                        return matches
        return matches

    return [list_files, read_file, grep]
