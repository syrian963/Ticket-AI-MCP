# SPDX-License-Identifier: MIT

"""Pointing at the files a subject is probably about.

This is a search, not an understanding. It reads the checkout for the words in
the subject and reports where they cluster - which is enough to save an
assistant a dozen blind greps, and nowhere near enough to say what the code
does. That part stays with whoever is reading.

Two things keep it honest and quick:

**It never opens what it cannot use.** Binaries, lockfiles, vendored trees,
migrations and anything past a size cap are skipped by path or by extension
before being read. On a Django project of any age, `node_modules` and
`migrations` together outnumber the source by an order of magnitude and
contain nothing anyone writes a ticket about.

**Filenames count for more than contents.** A file called
`destination_filter.py` is a better answer to "the destination filter is
broken" than a file that happens to mention the word twice, and no amount of
content matching recovers that.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .similar import tokens

# Extensions worth reading. Everything else is skipped unread - the point is
# to find code and templates, not to grep a repository exhaustively.
SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".vue",
        ".svelte",
        ".html",
        ".htm",
        ".jinja",
        ".j2",
        ".twig",
        ".erb",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".go",
        ".rb",
        ".php",
        ".java",
        ".kt",
        ".cs",
        ".rs",
        ".sql",
        ".graphql",
        ".proto",
        ".md",
        ".rst",
        ".txt",
        ".yml",
        ".yaml",
        ".toml",
        ".ini",
        ".cfg",
    }
)

# Directories that are never the answer. `migrations` is deliberate: a Django
# project's migration history mentions every model it ever had, so it matches
# almost any subject and means almost nothing.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
        "target",
        "vendor",
        "migrations",
        "locale",
        ".next",
        ".nuxt",
        ".idea",
        ".vscode",
        "coverage",
        "htmlcov",
        "static/admin",
        "site-packages",
    }
)

MAX_FILE_BYTES = 512_000
MAX_FILES_SCANNED = 20_000


@dataclass(frozen=True, slots=True)
class FileHit:
    """One file, and why it surfaced."""

    path: str
    score: float
    in_name: tuple[str, ...]
    in_body: tuple[str, ...]
    lines: int


def _candidates(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if Path(name).suffix.lower() not in SOURCE_SUFFIXES:
                continue
            found.append(Path(dirpath) / name)
            if len(found) >= MAX_FILES_SCANNED:
                return found
    return found


def search(root: Path, subject: str, *, limit: int = 12) -> list[FileHit]:
    """Find the files in `root` that mention what the subject is about.

    Terms come from the subject the same way they do for ticket similarity, so
    a subject written in prose still matches `destination_filter.py`.
    """
    terms = sorted(set(tokens(subject)))
    # A single-word subject matches half a codebase and helps nobody.
    terms = [t for t in terms if len(t) >= 3]
    if not terms or not root.is_dir():
        return []

    hits: list[FileHit] = []
    for path in _candidates(root):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        relative = path.relative_to(root).as_posix()
        name_words = set(tokens(relative))
        in_name = tuple(t for t in terms if t in name_words)

        lowered = text.lower()
        in_body = tuple(t for t in terms if t in lowered)
        if not in_name and not in_body:
            continue

        # A name match is worth far more than a body match, and matching
        # several distinct terms is worth more than matching one repeatedly -
        # a file that says "filter" forty times is not about destinations.
        score = len(in_name) * 4.0 + len(in_body) * 1.0
        hits.append(
            FileHit(
                path=relative,
                score=round(score, 2),
                in_name=in_name,
                in_body=in_body,
                lines=text.count("\n") + 1,
            )
        )

    hits.sort(key=lambda h: (-h.score, h.path))
    return hits[:limit]
